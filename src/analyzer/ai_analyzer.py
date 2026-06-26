"""
AI 选股分析模块
调用 LLM 对筛选出的股票生成选股解析、投资策略、买卖策略
支持 OpenAI 兼容 API
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

import httpx

from src.models.database import ScreeningResultDAO
from src.models.database import AiAnalysisLogDAO

logger = logging.getLogger(__name__)

# AI 分析 Prompt 模板
ANALYSIS_PROMPT = """
你是一位专业的价值投资分析师，基于格雷厄姆和巴菲特的价值投资理念，
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
}}
"""


class AiAnalyzer:
    """AI 选股分析器"""

    def __init__(self, config: dict):
        """
        config: {
            'api_base': 'https://api.openai.com/v1',
            'model': 'gpt-4o-mini',
            'temperature': 0.3,
            'max_tokens': 2000,
        }
        """
        self.cfg = config
        self.api_base = config.get('api_base', 'https://api.openai.com/v1')
        self.model = config.get('model', 'gpt-4o-mini')
        self.temperature = config.get('temperature', 0.3)
        self.max_tokens = config.get('max_tokens', 2000)

        # 从环境变量读取 API Key
        import os
        self.api_key = os.getenv('STOCK_AI_API_KEY') or os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            logger.warning("[AI分析] 未设置 API Key（STOCK_AI_API_KEY / OPENAI_API_KEY）")

    def analyze_stock(self, stock: dict) -> Optional[dict]:
        """
        对一只股票进行 AI 分析
        stock: {
            'code', 'name', 'pe', 'pb', 'roe',
            'revenue_growth', 'profit_growth', 'debt_ratio',
            'market_cap', 'sector', 'reason'
        }
        返回: {'analysis': ..., 'investment_strategy': ..., 'trade_strategy': ...}
        """
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

        try:
            response = self._call_llm(prompt)
            if not response:
                return None

            result = self._parse_response(response)
            return result

        except Exception as e:
            logger.error(f"[AI分析] {stock['code']} 分析失败: {e}")
            return None

    def _call_llm(self, prompt: str) -> Optional[str]:
        """调用 LLM API（含自动重试与退避）"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': '你是一位专业的价值投资分析师，精通A股市场分析。输出严格为JSON格式。'},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(
                        f'{self.api_base.rstrip("/")}/chat/completions',
                        headers=headers,
                        json=payload,
                    )
                    if resp.status_code == 429 and attempt < max_retries - 1:
                        wait = 5 * (attempt + 1)
                        logger.warning(f"[AI分析] 限流(429)，{wait}s后重试...")
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    content = data['choices'][0]['message']['content']
                    logger.debug(f"[AI分析] API 响应: {content[:100]}...")
                    return content
            except httpx.HTTPStatusError as e:
                if attempt < max_retries - 1 and e.response.status_code == 429:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"[AI分析] 限流(429)，{wait}s后重试...")
                    time.sleep(wait)
                    continue
                logger.error(f"[AI分析] API 调用失败: {e}")
                return None
            except httpx.RequestError as e:
                logger.error(f"[AI分析] 网络错误: {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                return None
        return None

    def _parse_response(self, content: str) -> Optional[dict]:
        """解析 LLM 返回的 JSON——兼容各种格式噪声"""
        # 1. 去掉 markdown 代码块包裹
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        elif '```' in content:
            # 取最后一个代码块
            blocks = content.split('```')
            for b in reversed(blocks):
                b = b.strip()
                if b.startswith('{'):
                    content = b
                    break
            else:
                content = blocks[1] if len(blocks) > 1 else content

        # 2. 去掉前后非 JSON 文本，只保留 {} 包裹的内容
        import re
        json_match = re.search(r'(\{.*\})', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # 3. 宽松解析
        try:
            result = json.loads(content, strict=False)
        except json.JSONDecodeError:
            # 尝试修复常见问题：末尾逗号、单引号
            cleaned = content.replace("'", '"')
            cleaned = re.sub(r',\s*}', '}', cleaned)
            cleaned = re.sub(r',\s*]', ']', cleaned)
            try:
                result = json.loads(cleaned, strict=False)
            except json.JSONDecodeError:
                logger.error(f"[AI分析] JSON 解析彻底失败，前200字符: {content[:200]}")
                return None

        # 4. 验证必要字段
        required = ['analysis', 'investment_strategy', 'trade_strategy']
        if not all(k in result for k in required):
            logger.warning(f"[AI分析] 返回字段不完整: {list(result.keys())}")
            return None
        return result


def run_ai_analysis(config: dict, candidates: list[dict]) -> list[dict]:
    """
    对筛选结果批量运行 AI 分析
    返回附带分析结果的增强列表
    """
    ai_config = config.get('ai', {})
    analyzer = AiAnalyzer(ai_config)
    result_dao = ScreeningResultDAO()
    run_id = candidates[0]['run_id'] if candidates else datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info(f"[AI分析] 开始分析 {len(candidates)} 只股票...")

    enhanced = []
    for i, stock in enumerate(candidates):
        logger.info(f"[AI分析] ({i+1}/{len(candidates)}) {stock['code']} {stock['name']}...")
        analysis = analyzer.analyze_stock(stock)

        if analysis:
            stock['ai_analysis'] = json.dumps(analysis, ensure_ascii=False)
            stock['ai_investment_strategy'] = analysis.get('investment_strategy', '')
            trade = analysis.get('trade_strategy', {})
            stock['ai_trade_strategy'] = json.dumps(trade, ensure_ascii=False)

            # 保存到数据库
            result_dao.update_ai_analysis(
                run_id, stock['code'],
                stock['ai_analysis'],
                stock['ai_investment_strategy'],
                stock['ai_trade_strategy'],
            )
        else:
            stock['ai_analysis'] = None
            stock['ai_investment_strategy'] = None
            stock['ai_trade_strategy'] = None

        enhanced.append(stock)

        # 请求间隔，避免限流
        if i < len(candidates) - 1:
            time.sleep(1)

    analyzed = sum(1 for s in enhanced if s.get('ai_analysis'))
    logger.info(f"[AI分析] 完成: 成功 {analyzed}/{len(candidates)} 只")
    return enhanced
