"""Stock Dashboard 连接处（只读、可降级）。

原则：
- 独立工作：Stock Dashboard 不存在/不可读时，所有函数回退到本地 config/空结果，永不抛错阻断交易链
- 合并工作：Stock Dashboard 在同一台机器上时，按需只读它的库（筛选候选、观察池、分析历史、基本面快照）
- 永不写母库：连接一律 SQLite URI只读模式；写操作只发生在自家 DB
"""
from .analysis import read_market_regime, read_stock_cards
from .mother import (benchmark, compare_bars, coverage, daily_bars, dividend,
                     financial_summary, instrument_map, news, outcomes, regime,
                     screening_detail, thesis, trading_calendar)
from .pool import resolve_pool
from .settings import is_merged, mother_db_path, mother_dir
from .sources import (SRC_LOCAL_AKSHARE, SRC_LOCAL_CONFIG, SRC_LOCAL_SINA,
                      SRC_LOCAL_TENCENT, SRC_MOTHER, SRC_UNAVAILABLE, block,
                      mother_source)
from .universe import is_a_share, search_symbols, universe_size

__all__ = [
    "is_merged", "mother_dir", "mother_db_path",
    "resolve_pool", "read_stock_cards", "read_market_regime",
    "search_symbols", "is_a_share", "universe_size",
    "daily_bars", "financial_summary", "dividend", "benchmark", "news",
    "screening_detail", "thesis", "outcomes", "regime",
    "instrument_map", "trading_calendar", "coverage", "compare_bars",
    "SRC_LOCAL_AKSHARE", "SRC_LOCAL_TENCENT", "SRC_LOCAL_SINA",
    "SRC_LOCAL_CONFIG", "SRC_MOTHER", "SRC_UNAVAILABLE", "block",
    "mother_source",
]
