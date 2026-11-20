"""只读连接与列契约（E0）。

- `connect()` 用 SQLite URI `mode=ro` + `PRAGMA query_only=1`，物理上杜绝误写；
- `missing_columns()` 用 `PRAGMA table_info` 做列存在性校验（子项目没有可靠的
  schema 版本表），缺列/缺表时调用方返回 `unavailable` 而不是抛异常。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

#: 纸盘桥 v1 契约（docs/paper-bridge.md §3）
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "account": ("cash", "initial_cash"),
    "positions": ("symbol", "total_volume", "available_volume", "avg_cost"),
    "orders": ("order_id", "symbol", "direction", "volume", "status",
               "filled_price", "commission", "stamp_duty", "created_at"),
    "fills": ("fill_id", "order_id", "symbol", "direction", "volume", "price",
              "commission", "stamp_duty", "timestamp"),
    "nav_history": ("timestamp", "cash", "market_value", "total_value",
                    "pnl", "pnl_pct"),
    "agent_plans": ("id", "plan_date", "symbols", "plan_json", "status",
                    "created_at", "executed_at"),
}


def connect(path: Path | str, timeout: float = 1.0) -> sqlite3.Connection | None:
    """只读连接；不存在/不可读返回 None。"""
    p = Path(path)
    if not p.is_file():
        return None
    uri = f"file:{quote(str(p).replace(chr(92), '/'))}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")
        return conn
    except sqlite3.Error:
        return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def missing_columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """返回 `{table: [缺失列...]}`；空 dict 表示契约满足。"""
    out: dict[str, list[str]] = {}
    for table, required in REQUIRED_COLUMNS.items():
        cols = _columns(conn, table)
        if not cols:
            out[table] = ["<table missing>"]
            continue
        miss = [c for c in required if c not in cols]
        if miss:
            out[table] = miss
    return out
