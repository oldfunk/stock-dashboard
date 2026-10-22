"""规则引擎（盘中量化执行层）：规则判定 / 配置 / 回测 / 守护进程。

定位（与 LLM 分工）：
- 规则引擎：快、免费、可回测，负责**执行与择时**
- LLM（paper_trading.agent）：慢、贵，负责**选股与策略设计**（含条件单）
两者共用同一套风控（RiskManager）与撮合（PaperBroker）。
"""
from .config import load_engine_config, save_engine_config, validate
from .rules import (Intent, PositionView, RULE_TYPES, RuleContext, evaluate,
                    rsi, size_for_value)

__all__ = [
    "load_engine_config", "save_engine_config", "validate",
    "Intent", "PositionView", "RuleContext", "RULE_TYPES", "evaluate", "rsi",
    "size_for_value",
]