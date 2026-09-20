"""Hermes 代理服务主模块

提供 POST /api/analyze 端点，接收股票数据 → 调 LLM → 返回结构化 JSON。
配置通过 config.yaml 的 hermes_proxy 段读取，不硬编码任何设备地址。
"""

import json
import logging
import time
from typing import Optional

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="Hermes Proxy AI Analysis", version="1.0.0")


# ── 配置加载 ──

def load_config(config_path: str = "config/config.yaml") -> dict:
    """加载 config.yaml 中的 hermes_proxy 段"""
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    return cfg.get("hermes_proxy", {})


# ── 请求/响应模型 ──

class AnalyzeRequest(BaseModel):
    code: str
    name: str
    run_id: str
    data: dict


class AnalyzeResponse(BaseModel):
    status: str
    model: Optional[str] = None
    analysis: Optional[dict] = None
    usage: Optional[dict] = None
    error: Optional[str] = None
    retryable: Optional[bool] = None


# ── LLM 调用 ──

def _call_llm(prompt: str, api_base: str, model: str,
              api_key: Optional[str] = None, timeout: int = 300) -> dict:
    """调用 OpenAI 兼容 LLM API"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 6000,
    }
    url = api_base.rstrip("/") + "/chat/completions"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    # 尝试解析 JSON
    try:
        analysis = json.loads(content)
    except json.JSONDecodeError:
        # 尝试从 markdown 代码块提取
        if "```json" in content:
            block = content.split("```json")[1].split("```")[0]
            analysis = json.loads(block)
        else:
            analysis = {"raw": content}
    usage = data.get("usage", {})
    return {
        "analysis": analysis,
        "model": data.get("model", model),
        "usage": {
            "tokens": usage.get("total_tokens", 0),
            "cost_usd": 0.0,  # OpenAI 兼容 API 通常不返回 cost
        },
    }


def _build_prompt(req: AnalyzeRequest) -> str:
    """构建 AI 分析 prompt（复用现有 ai_analyzer 的逻辑）"""
    data = req.data
    return f"""你是一位价值投资基金经理，请分析以下股票并输出 JSON 格式结果。

股票：{req.name}（{req.code}）
运行 ID：{req.run_id}

数据：
- 评分：{data.get('score', 'N/A')}
- PE：{data.get('pe', 'N/A')}
- PB：{data.get('pb', 'N/A')}
- ROE 5年均：{data.get('roe_5y_avg', 'N/A')}%
- 毛利率：{data.get('gross_margin', 'N/A')}%
- 净利率：{data.get('net_margin', 'N/A')}%
- 负债率：{data.get('debt_ratio', 'N/A')}%
- 营收增长：{data.get('revenue_growth', 'N/A')}%
- 利润增长：{data.get('profit_growth', 'N/A')}%
- FCF 5年累计：{data.get('fcf_5y_sum', 'N/A')}
- 市值：{data.get('market_cap', 'N/A')}亿

请输出以下 JSON 结构：
{{
  "analysis": "分析文本",
  "moat_evaluation": [{{"type": "护城河类型", "score": 1-5, "summary": "说明"}}],
  "management_score": {{"capital_allocation": 1-10, "shareholder_friendliness": 1-10, "summary": "说明"}},
  "intrinsic_value": {{"conservative": 亿, "base_case": 亿, "optimistic": 亿, "method": "方法"}},
  "trade_strategy": {{"signal": "BUY/HOLD/AVOID", "confidence": "high/medium/low", "buy_zone": "区间", "target_price": 价, "stop_loss": 价, "take_profit": 价}},
  "veto_checklist": {{"triggered_count": 0, "cannot_explain_business": false, "negative_fcf_3y_no_improvement": false, "management_integrity_issue": false, "moat_eroding_irreversibly": false, "greater_fool_required": false, "cannot_afford_total_loss": false, "following_the_herd": false, "cannot_write_200_char_thesis": false}}
}}"""


# ── 端点 ──

@app.post("/api/analyze", response_model=AnalyzeResponse)
async def api_analyze(req: AnalyzeRequest):
    """AI 分析端点：接收股票数据 → 调 LLM → 返回结构化 JSON"""
    cfg = load_config()
    primary = cfg.get("primary", {})
    fallback = cfg.get("fallback", {})

    prompt = _build_prompt(req)

    # 主通道
    try:
        result = _call_llm(
            prompt=prompt,
            api_base=primary.get("api_base", ""),
            model=primary.get("model", ""),
            api_key=primary.get("api_key"),
            timeout=primary.get("timeout", 300),
        )
        return AnalyzeResponse(
            status="ok",
            model=result["model"],
            analysis=result["analysis"],
            usage=result["usage"],
        )
    except Exception as e:
        logger.warning(f"[HermesProxy] 主通道失败: {e}")

    # 降级通道
    if fallback.get("api_base"):
        try:
            result = _call_llm(
                prompt=prompt,
                api_base=fallback["api_base"],
                model=fallback.get("model", ""),
                api_key=fallback.get("api_key"),
                timeout=fallback.get("timeout", 300),
            )
            return AnalyzeResponse(
                status="ok",
                model=result["model"],
                analysis=result["analysis"],
                usage=result["usage"],
            )
        except Exception as e:
            logger.error(f"[HermesProxy] 降级通道也失败: {e}")

    return AnalyzeResponse(
        status="error",
        error="LLM 调用失败（主通道+降级通道均不可用）",
        retryable=True,
    )


@app.get("/api/health")
async def api_health():
    """健康检查"""
    return {"status": "ok", "service": "hermes_proxy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
