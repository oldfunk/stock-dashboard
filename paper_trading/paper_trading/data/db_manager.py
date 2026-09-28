"""SQLite 数据库管理器。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from paper_trading.models import Bar
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class DataDBManager:
    """行情数据库管理器。"""

    def __init__(self, db_path: str | Path = "data.db") -> None:
        self.db_path = Path(db_path)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_bars (
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    turn REAL DEFAULT 0.0,
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bars_symbol_time
                ON daily_bars(symbol, timestamp)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_pool (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    added_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def upsert_bars(self, bars: list[Bar]) -> int:
        """批量插入或更新K线数据。"""
        if not bars:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO daily_bars
                   (symbol, timestamp, open, high, low, close, volume, turn)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (b.symbol, b.timestamp.isoformat(), b.open, b.high,
                     b.low, b.close, b.volume, b.turn)
                    for b in bars
                ],
            )
        logger.info(f"Upserted {len(bars)} bars into {self.db_path}")
        return len(bars)

    def get_bars(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Bar]:
        """查询指定股票的K线数据（limit 取最近 N 根，按时间升序返回）。"""
        sql = "SELECT * FROM daily_bars WHERE symbol = ?"
        params: list = [symbol]
        if start:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end:
            sql += " AND timestamp <= ?"
            params.append(end)
        if limit:
            # 先取最近 N 根（DESC），再按时间升序返回，避免 ASC+LIMIT 取到最老数据
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
        else:
            sql += " ORDER BY timestamp ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        bars = [
            Bar(
                symbol=r["symbol"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                turn=r["turn"],
            )
            for r in rows
        ]
        if limit:
            bars.reverse()
        return bars

    def get_latest_timestamp(self, symbol: str) -> Optional[str]:
        """获取某股票最新的数据时间戳。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM daily_bars WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def add_stock_to_pool(self, symbol: str, name: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO stock_pool (symbol, name) VALUES (?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     name = CASE WHEN excluded.name != '' THEN excluded.name
                                 ELSE stock_pool.name END""",
                (symbol, name),
            )

    def upsert_stock_names(self, names: dict[str, str]) -> int:
        """批量写入/刷新股票名称（真实名称，来自行情源）。"""
        n = 0
        for sym, nm in names.items():
            if nm:
                self.add_stock_to_pool(sym, nm)
                n += 1
        if n:
            logger.info(f"Updated {n} stock names")
        return n

    def get_stock_names(self) -> dict[str, str]:
        """读取已缓存的 {symbol: 名称}。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, name FROM stock_pool WHERE name IS NOT NULL AND name != ''"
            ).fetchall()
        return {r["symbol"]: r["name"] for r in rows}

    def get_stock_pool(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM stock_pool").fetchall()
        return [dict(r) for r in rows]

    def get_pool_symbols(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT symbol FROM stock_pool").fetchall()
        return [r["symbol"] for r in rows]

    def get_data_asof(self) -> Optional[str]:
        """全库最新 K 线日期（YYYY-MM-DD），无数据返回 None。"""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(timestamp) FROM daily_bars").fetchone()
        if not row or not row[0]:
            return None
        return str(row[0])[:10]
