"""AI 观察池复盘引擎

混合机制：硬规则预过滤 + LLM 自主决策。

对外暴露：
- WatchlistReviewer 类：单次复盘入口 review()
- 4 个硬规则检查函数：check_signal_avoid / check_roe_below /
  check_price_above_buyzone / check_roe_collapse
"""

import json
import logging
import threading
from typing import Optional

from src.utils import now_cn

logger = logging.getLogger(__name__)


# ── 4 条硬规则 ──

def check_signal_avoid(stock: dict, analysis: Optional[dict]) -> bool:
    """规则 1: Signal=AVOID → 强制调出"""
    if not analysis:
        return False
    trade = analysis.get('trade_strategy', {})
    if isinstance(trade, str):
        try:
            trade = json.loads(trade)
        except (json.JSONDecodeError, TypeError):
            return False
    return trade.get('signal', '').upper() == 'AVOID'


def check_roe_below(stock: dict, analysis: Optional[dict],
                    threshold: float = 5) -> bool:
    """规则 2: ROE 跌破阈值 → 强制调出"""
    roe = stock.get('roe')
    if roe is None:
        return False
    return roe < threshold


def check_price_above_buyzone(stock: dict, analysis: Optional[dict],
                               pct: float = 20) -> bool:
    """规则 3: 当前价 > 买入区上限 +pct% → 强制调出（已涨过头）

    买入区格式形如 "1500-1700"，取上限 1700 计算。
    """
    if not analysis:
        return False
    trade = analysis.get('trade_strategy', {})
    if isinstance(trade, str):
        try:
            trade = json.loads(trade)
        except (json.JSONDecodeError, TypeError):
            return False
    buy_zone = trade.get('buy_zone')
    if not buy_zone or not isinstance(buy_zone, str):
        return False
    current_price = stock.get('current_price')
    if current_price is None:
        return False
    # 解析 "1500-1700" 或 "1500~1700"
    parts = buy_zone.replace('~', '-').split('-')
    if len(parts) != 2:
        return False
    try:
        upper = float(parts[1].strip())
    except (ValueError, IndexError):
        return False
    return current_price > upper * (1 + pct / 100)


def check_roe_collapse(stock: dict, analysis: Optional[dict],
                       pp: float = 10) -> bool:
    """规则 4: ROE 同比下降 > pp 个百分点 → 强制调出

    依赖 _history_roe.last_year 字段（由 _load_history_roe 注入）。
    """
    history = stock.get('_history_roe')
    if not history:
        return False
    last_year = history.get('last_year')
    current = stock.get('roe')
    if last_year is None or current is None:
        return False
    return (last_year - current) > pp


# ── 复盘引擎 ──

class WatchlistReviewer:
    """AI 观察池复盘引擎

    单一职责：输入数据 → 输出复盘结果 + 落库。
    模块级锁防并发，跟每日流水线锁 _pipeline_lock 独立。
    """

    _module_lock = threading.Lock()

    def __init__(self, config: dict):
        self.cfg = config or {}
        ai_review_cfg = self.cfg.get('ai_review', {})
        self.hard_rules_cfg = ai_review_cfg.get('hard_rules', {})
        self.watchlist_size = ai_review_cfg.get('watchlist_size', 5)
        self.candidate_weeks = ai_review_cfg.get('candidate_pool_weeks', 4)

    def _apply_hard_rules(self, current: list[dict],
                          analyses: dict[str, dict]) -> list[dict]:
        """对当前观察池应用 4 条硬规则，返回必须调出的股票列表。

        返回: [{"code": "002415", "name": "海康威视", "reasons": ["signal_avoid", ...]}]
        """
        forced_out = []
        for stock in current:
            code = stock['code']
            analysis = analyses.get(code)
            reasons = []

            if self.hard_rules_cfg.get('signal_avoid', True):
                if check_signal_avoid(stock, analysis):
                    reasons.append('signal_avoid')

            threshold = self.hard_rules_cfg.get('roe_below', 5)
            if check_roe_below(stock, analysis, threshold=threshold):
                reasons.append(f'roe_below_{threshold}')

            pct = self.hard_rules_cfg.get('price_above_buyzone_pct', 20)
            if check_price_above_buyzone(stock, analysis, pct=pct):
                reasons.append(f'price_above_buyzone_{pct}')

            pp = self.hard_rules_cfg.get('roe_collapse_pp', 10)
            if check_roe_collapse(stock, analysis, pp=pp):
                reasons.append(f'roe_collapse_{pp}')

            if reasons:
                forced_out.append({
                    'code': code,
                    'name': stock.get('name', ''),
                    'reasons': reasons,
                })
        return forced_out

    # 下面的方法在后续 Task 实现
    def review(self, run_id: str) -> dict:
        """复盘主入口（Task 4 实现）"""
        raise NotImplementedError("review() 在 Task 4 实现")

    def _build_prompt(self, *args, **kwargs) -> str:
        """构建 LLM prompt（Task 3 实现）"""
        raise NotImplementedError("_build_prompt() 在 Task 3 实现")

    def _call_llm(self, prompt: str) -> Optional[dict]:
        """调用 LLM 并解析 JSON（Task 3 实现）"""
        raise NotImplementedError("_call_llm() 在 Task 3 实现")

    def _validate_and_persist(self, *args, **kwargs) -> dict:
        """校验 + 落库（Task 4 实现）"""
        raise NotImplementedError("_validate_and_persist() 在 Task 4 实现")
