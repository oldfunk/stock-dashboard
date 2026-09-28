"""母项目连接处（只读、可降级）。

原则：
- 独立工作：母项目不存在/不可读时，所有函数回退到本地 config/空结果，永不抛错阻断交易链
- 合并工作：母项目在同一台机器上时，按需只读它的库（筛选候选、观察池、分析历史、基本面快照）
- 永不写母库：连接一律 SQLite URI只读模式；写操作只发生在自家 DB
"""
from .analysis import read_market_regime, read_stock_cards
from .pool import resolve_pool
from .settings import is_merged, mother_db_path, mother_dir

__all__ = [
    "is_merged", "mother_dir", "mother_db_path",
    "resolve_pool", "read_stock_cards", "read_market_regime",
]
