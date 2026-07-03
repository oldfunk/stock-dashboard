"""
AI 选股分析模块（项目唯一的 AI 调用实现）

调用 LLM 对筛选出的股票生成：选股解析 / 投资策略 / 买卖策略。
所有脚本（run_pipeline.py / run_ai_analysis.py）都应通过本模块调用 AI，
避免多套重试策略与默认值不一致的问题。

设计要点：
- 20 次指数退避（30→60→120→240→300×16），应对 GLM-4.7-Flash 免费模型的拥堵
- 健壮 JSON 解析（兼容 markdown 包裹 / 前后噪声文本 / 末尾逗号 / 单引号 / 截断）
- 默认 API/模型与 config.yaml 对齐（智谱 glm-4.7-flash），可被环境变量覆盖
"""

import json
import logging
import os
import re
import time
from typing import Optional, Callable

import httpx

logger = logging.getLogger(__name__)


# 20 次指数退避表（秒）。GLM-4.7-Flash 拥堵时持续等待绝不放弃
BACKOFF_SCHEDULE: list[int] = [
    30, 60, 120, 240,
    300, 300, 300, 300, 300, 300,
    300, 300, 300, 300, 300, 300,
    300, 300, 300, 300,
]

# 默认配置与 config.yaml / README 保持一致
DEFAULT_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4.7-flash"

# AI 分析 Prompt 模板
ANALYSIS_PROMPT = """你是一位专业的价值投资分析师，基于格雷厄姆和巴菲特的价值投资理念，
对以下A股股票进行深度分析并给出投资建议。

## 股票基本面数据
- 股票名称：{name}（{code}）
- 市盈率 PE：{pe}
- 市净率 PB：{pb}
- ROE（净资产收益率）：{roe}%
- 营收增长率：{revenue_growth}%
- 净利润增长率：{profit_growth}%
- 资产负债率：{debt_ratio}%
- 总市值：{market_cap}亿
- 所属行业：{sector}

## 选股理由
{reason}

## 输出格式（JSON）
请严格按以下 JSON 格式输出，不要包含其他内容：

{{
    "analysis": "选股解析——分析这只股票为什么符合价值投资标准，竞争优势（护城河）、行业地位、财务健康度、潜在风险。300-500字。",
    "investment_strategy": "投资策略——建议仓位配置、持有周期、适合什么类型的投资者。100-200字。",
    "trade_strategy": {{
        "buy_zone": "买入区间——基于估值判断的合理买入价格区间",
        "target_price": "目标价——基于PE/PB估值模型的目标价格",
        "stop_loss": "止损线——基本面或价格止损条件",
        "take_profit": "止盈条件——分批止盈触发条件"
    }}
}}"""


def parse_ai_response(content: str) -> Optional[dict]:
    """健壮地解析 LLM 返回的 JSON。

    兼容：markdown 代码块包裹、前后噪声文本、末尾逗号、单引号、截断补全。
    返回包含 analysis / investment_strategy / trade_strategy 的 dict，失败返回 None。
    """
    if not content:
        return None

    text = content.strip()

    # 1. 去掉 markdown 代码块包裹
    if '```json' in text:
        text = text.split('```json', 1)[1]
        text = text.split('```', 1)[0].strip()
    elif '```' in text:
        blocks = text.split('```')
        # 取最后一个以 { 开头的代码块
        extracted = None
        for b in reversed(blocks):
            b = b.strip()
            if b.startswith('{'):
                extracted = b
                break
        text = extracted if extracted else (blocks[1] if len(blocks) > 1 else text)

    # 2. 截取第一个 { 到最后一个 } 之间的内容
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        text = m.group(1)

    # 3. 直接解析
    try:
        result = json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 4. 修复常见问题：末尾逗号、单引号、截断补全
        cleaned = text.replace("'", '"')
        cleaned = re.sub(r',\s*}', '}', cleaned)
        cleaned = re.sub(r',\s*]', ']', cleaned)
        if cleaned.startswith('{') and not cleaned.endswith('}'):
            cleaned += '}'
        try:
            result = json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            logger.error(f"[AI分析] JSON 解析失败，前200字符: {text[:200]}")
            return None

    # 5. 校验必要字段
    required = ['analysis', 'investment_strategy', 'trade_strategy']
    if not all(k in result for k in required):
        logger.warning(f"[AI分析] 返回字段不完整: {list(result.keys())}")
        return None
    return result


class AiAnalyzer:
    """AI 选股分析器（项目唯一实现）。"""

    def __init__(self, config: dict = None):
        """
        Args:
            config: config.yaml 中 ai 段，形如::

                {
                    'api_base': 'https://open.bigmodel.cn/api/paas/v4',
                    'model': 'glm-4.7-flash',
                    'temperature': 0.3,
                    'max_tokens': 2000,
                }

            环境变量优先于 config：STOCK_AI_API_KEY / STOCK_AI_API_BASE / STOCK_AI_MODEL
        """
        cfg = config or {}
        self.api_base = (
            os.getenv('STOCK_AI_API_BASE')
            or cfg.get('api_base')
            or DEFAULT_API_BASE
        ).rstrip('/')
        self.model = os.getenv('STOCK_AI_MODEL') or cfg.get('model') or DEFAULT_MODEL
        self.temperature = cfg.get('temperature', 0.3)
        self.max_tokens = cfg.get('max_tokens', 2000)

        self.api_key = os.getenv('STOCK_AI_API_KEY') or os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            logger.warning("[AI分析] 未设置 API Key（STOCK_AI_API_KEY / OPENAI_API_KEY）")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def analyze_stock(self, stock: dict) -> Optional[dict]:
        """对一只股票进行 AI 分析，返回解析后的 dict（失败返回 None）。"""
        if not self.api_key:
            logger.error("[AI分析] 无 API Key，跳过分析")
            return None

        prompt = ANALYSIS_PROMPT.format(
            name=stock.get('name', ''),
            code=stock.get('code', ''),
            pe=stock.get('pe', 'N/A'),
            pb=stock.get('pb', 'N/A'),
            roe=stock.get('roe', 'N/A'),
            revenue_growth=stock.get('revenue_growth', 'N/A'),
            profit_growth=stock.get('profit_growth', 'N/A'),
            debt_ratio=stock.get('debt_ratio', 'N/A'),
            market_cap=stock.get('market_cap', 'N/A'),
            sector=stock.get('sector', '未知'),
            reason=stock.get('reason', ''),
        )

        content = self._call_llm(prompt)
        if not content:
            return None
        return parse_ai_response(content)

    def _call_llm(self, prompt: str) -> Optional[str]:
        """调用 LLM API，使用 BACKOFF_SCHEDULE 做 20 次退避重试。"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system',
                 'content': '你是一位专业的价值投资分析师，精通A股市场分析。输出严格为JSON格式。'},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
        }
        url = f"{self.api_base}/chat/completions"
        max_retries = len(BACKOFF_SCHEDULE)

        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(url, headers=headers, json=payload)

                if resp.status_code == 200:
                    try:
                        content = resp.json()['choices'][0]['message']['content']
                    except (KeyError, IndexError, ValueError) as e:
                        logger.warning(f"[AI分析] 200但响应结构异常: {e}")
                        time.sleep(BACKOFF_SCHEDULE[attempt])
                        continue
                    if not content or len(content) < 10:
                        logger.warning(
                            f"[AI分析] 响应内容过短({len(content)}字符), 重试...")
                        time.sleep(BACKOFF_SCHEDULE[attempt])
                        continue
                    return content

                if resp.status_code == 429:
                    # 智谱常返回 code:1305（访问量过大）
                    self._log_rate_limit(resp, attempt, max_retries)
                    time.sleep(BACKOFF_SCHEDULE[attempt])
                    continue

                if resp.status_code == 503:
                    logger.warning(
                        f"[AI分析] 503, 退避 {BACKOFF_SCHEDULE[attempt]}s")
                    time.sleep(BACKOFF_SCHEDULE[attempt])
                    continue

                logger.warning(
                    f"[AI分析] HTTP {resp.status_code}, 退避 {BACKOFF_SCHEDULE[attempt]}s")
                time.sleep(BACKOFF_SCHEDULE[attempt])

            except httpx.TimeoutException:
                logger.warning(
                    f"[AI分析] 超时, 退避 {BACKOFF_SCHEDULE[attempt]}s")
                time.sleep(BACKOFF_SCHEDULE[attempt])
            except httpx.RequestError as e:
                logger.warning(f"[AI分析] 网络错误: {e}, 退避 {BACKOFF_SCHEDULE[attempt]}s")
                time.sleep(BACKOFF_SCHEDULE[attempt])
            except Exception as e:
                logger.warning(f"[AI分析] 异常: {e}, 退避 {BACKOFF_SCHEDULE[attempt]}s")
                time.sleep(BACKOFF_SCHEDULE[attempt])

        logger.error(f"[AI分析] 已达最大重试次数 {max_retries}，放弃")
        return None

    @staticmethod
    def _log_rate_limit(resp: httpx.Response, attempt: int, max_retries: int):
        try:
            err = resp.json().get('error', {})
            err_code = err.get('code', '')
            if err_code == '1305':
                logger.warning(
                    f"[AI分析] 拥挤1305({err.get('message','')}) 退避 {attempt+1}/{max_retries}")
                return
        except Exception:
            pass
        logger.warning(
            f"[AI分析] 限流(429), 退避 {BACKOFF_SCHEDULE[attempt]}s ({attempt+1}/{max_retries})")


# ── 批量分析 ──

def _save_analysis(stock: dict, result: dict, run_id: str):
    """把 AI 分析结果写入 screening_result 与 stock_analysis_history。"""
    from src.models.database import ScreeningResultDAO, StockAnalysisHistoryDAO

    analysis_json = json.dumps(result, ensure_ascii=False)
    strategy = result.get('investment_strategy', '')
    if isinstance(strategy, dict):
        strategy = json.dumps(strategy, ensure_ascii=False)
    trade = result.get('trade_strategy', {})
    trade_json = json.dumps(trade, ensure_ascii=False) if isinstance(trade, dict) else str(trade)

    ScreeningResultDAO().update_ai_analysis(
        run_id, stock['code'], analysis_json, strategy, trade_json)
    StockAnalysisHistoryDAO().save(
        stock['code'], run_id, stock.get('score'),
        analysis_json, trade_json)


def _save_failure(stock: dict, run_id: str):
    """记录一次失败尝试（写入空记录，避免短时间内重复尝试）。"""
    from src.models.database import StockAnalysisHistoryDAO
    StockAnalysisHistoryDAO().save(
        stock['code'], run_id, stock.get('score'), '{}', '{}')


def analyze_batch(stocks: list[dict], run_id: str,
                  interval_seconds: int = 60,
                  on_progress: Optional[Callable[[int, int, int], None]] = None
                  ) -> tuple[int, int]:
    """批量分析股票并持久化结果。

    Args:
        stocks: 待分析股票列表（需含 code/name/score 等字段）
        run_id: 运行批次 ID
        interval_seconds: 每只股票之间的冷却间隔（默认 60s，避免限流）
        on_progress: 进度回调 (analyzed_ok, analyzed_failed, current_index)

    Returns:
        (成功数, 失败数)
    """
    from src.config import load_config
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    total = len(stocks)
    if total == 0:
        return 0, 0
    if not analyzer.configured:
        logger.error("[AI分析] 无 API Key，跳过全部")
        for i, s in enumerate(stocks):
            _save_failure(s, run_id)
        return 0, total

    logger.info(f"[AI分析] 开始分析 {total} 只股票...")
    analyzed_ok = 0
    analyzed_failed = 0

    for idx, stock in enumerate(stocks):
        code, name = stock.get('code'), stock.get('name')
        logger.info(f"[AI分析] ({idx+1}/{total}) {code} {name}...")
        if on_progress:
            on_progress(analyzed_ok, analyzed_failed, idx)

        result = analyzer.analyze_stock(stock)
        if result:
            _save_analysis(stock, result, run_id)
            analyzed_ok += 1
            logger.info(f"  ✅ {analyzed_ok}/{total}")
        else:
            _save_failure(stock, run_id)
            analyzed_failed += 1
            logger.warning(f"  ✗ Failed {analyzed_failed}")

        # 股票之间冷却
        if idx < total - 1:
            time.sleep(interval_seconds)

    if on_progress:
        on_progress(analyzed_ok, analyzed_failed, total)
    logger.info(
        f"[AI分析] 完成: 成功 {analyzed_ok}/{total}（失败 {analyzed_failed}）")
    return analyzed_ok, analyzed_failed
