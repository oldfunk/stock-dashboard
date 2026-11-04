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
        #: 本次打开真正执行过的结构迁移（T38：CLI 拿它写 `op_log[data:migrate]`）
        self.migrations: list[dict] = []
        self._init_schema()

    def _record_migration(self, **rec) -> None:
        rec.setdefault("at", datetime.now().isoformat())
        rec.setdefault("db", str(self.db_path))
        self.migrations.append(rec)
        logger.info("data 库结构迁移: %s", rec)

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

    @staticmethod
    def _has_column(conn, table: str, column: str) -> bool:
        return any(r["name"] == column
                   for r in conn.execute(f"PRAGMA table_info({table})"))

    @staticmethod
    def _pk_columns(conn, table: str) -> list:
        return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")
                if r["pk"]]

    def _migrate_minute_pk(self, conn) -> dict:
        """T35（§5.5 裁决 6）：`minute_bars` 主键补 `period`。

        旧主键 `(symbol, ts)` 会让**同一时刻的 1 分钟与 5 分钟互相覆盖**
        （`ON CONFLICT` 静默改写）——这是钱类数据层的定时炸弹，现在拆。
        安全顺序：先备份文件 → 建新表 → 整表搬运 → **行数核对** → 才 drop/rename；
        任一步异常由 `_connect()` 回滚，旧表原样保留。
        迁移只做一次：新表主键已含 period 时直接跳过。
        """
        table = "minute_bars"
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,)).fetchone():
            return {"migrated": False, "reason": "no_table"}
        if "period" in self._pk_columns(conn, table):
            return {"migrated": False, "reason": "already"}

        bak = self.db_path.with_name(self.db_path.name + ".pre-t35.bak")
        if not bak.exists():
            import contextlib

            with contextlib.closing(sqlite3.connect(str(bak))) as dst:
                conn.backup(dst)

        n_old = int(conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"])
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        want = ["symbol", "ts", "open", "high", "low", "close", "volume", "amount",
                "adjust", "period"]
        select = ", ".join(c if c in cols else f"'{v}' AS {c}"
                           for c, v in zip(want, ["", "", 0, 0, 0, 0, 0, 0,
                                                   "raw", "5"]))
        conn.execute(f"""CREATE TABLE {table}_t35 (
            symbol TEXT NOT NULL, ts TEXT NOT NULL, open REAL NOT NULL,
            high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
            volume REAL DEFAULT 0, amount REAL DEFAULT 0,
            adjust TEXT DEFAULT 'raw', period TEXT NOT NULL DEFAULT '5',
            PRIMARY KEY (symbol, ts, period))""")
        conn.execute(f"INSERT INTO {table}_t35 ({', '.join(want)}) SELECT {select} "
                     f"FROM {table}")
        n_new = int(conn.execute(f"SELECT COUNT(*) c FROM {table}_t35").fetchone()["c"])
        if n_new != n_old:
            raise RuntimeError(
                f"minute_bars 主键迁移中止：搬运行数不一致（旧 {n_old} → 新 {n_new}），"
                f"旧表保持原样，已备份 {bak.name}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_t35 RENAME TO {table}")
        conn.execute(f"""CREATE INDEX IF NOT EXISTS idx_minute_symbol_ts
                         ON {table}(symbol, ts)""")
        logger.info("minute_bars 主键已升级为 (symbol, ts, period)：%d 行迁移完成，"
                    "备份见 %s", n_new, bak.name)
        self._record_migration(table=table, kind="pk_rebuild",
                              pk=["symbol", "ts", "period"],
                              rows=n_new, backup=bak.name)
        return {"migrated": True, "rows": n_new, "backup": bak.name}

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
                    adjust TEXT DEFAULT 'qfq',
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bars_symbol_time
                ON daily_bars(symbol, timestamp)
            """)
            # T31 G-T13-1 口径标签：既有 daily_bars 一直是**前复权**
            # （akshare_fetcher.py 默认 adjust="qfq"），加列打标签而不改写任何值——
            # AI 展示层还靠这口径，引擎侧一律改读 daily_bars_raw。
            # 新建库的 CREATE 已带 adjust，下面这条 ALTER 只服务 T31 之前建的老库；
            # 留痕（T38 批复 ②）也因此只会在"真的动过既有库"时才有内容。
            if not self._has_column(conn, "daily_bars", "adjust"):
                conn.execute("ALTER TABLE daily_bars ADD COLUMN adjust TEXT DEFAULT 'qfq'")
                self._record_migration(table="daily_bars", kind="add_column",
                                      column="adjust", default="qfq")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_bars_raw (
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    amount REAL,
                    turn REAL DEFAULT 0.0,
                    adjust TEXT NOT NULL DEFAULT 'raw',
                    source TEXT,
                    checked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_raw_symbol_time
                ON daily_bars_raw(symbol, timestamp)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_pool (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    added_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS minute_bars (
                    symbol TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL DEFAULT 0,
                    amount REAL DEFAULT 0,
                    adjust TEXT DEFAULT 'raw',
                    period TEXT NOT NULL DEFAULT '5',
                    PRIMARY KEY (symbol, ts, period)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_minute_symbol_ts
                ON minute_bars(symbol, ts)
            """)
            if not self._has_column(conn, "minute_bars", "adjust"):
                conn.execute("ALTER TABLE minute_bars ADD COLUMN adjust TEXT DEFAULT 'raw'")
                self._record_migration(table="minute_bars", kind="add_column",
                                      column="adjust", default="raw")
            if not self._has_column(conn, "minute_bars", "period"):
                conn.execute("ALTER TABLE minute_bars ADD COLUMN period TEXT DEFAULT '5'")
                self._record_migration(table="minute_bars", kind="add_column",
                                      column="period", default="5")
            self._migrate_minute_pk(conn)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS quote_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    price REAL,
                    prev_close REAL,
                    volume REAL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_quote_symbol_ts
                ON quote_log(symbol, ts)
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
                adjust=r["adjust"] if "adjust" in r.keys() else None,
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

    # ---- raw 口径（T31：引擎只准读这里）--------------------------------
    def upsert_raw_bars(self, bars: list[Bar], source: str = "",
                        checked: bool = False,
                        checked_dates: Optional[set] = None) -> int:
        """写入原始价日线。`adjust` 必须真是 'raw'，否则拒写（fail-closed）。

        `checked_dates`（{'YYYY-MM-DD'}）逐行标记"该日已与独立源同日对照通过"——
        母库最新日的 `close_raw` 实测为 NULL（20/20 只），所以整批一刀切会把
        已验证的旧日一起降级，逐行标记才可用；下一轮补跑会自动把该日翻成已验证。
        """
        if not bars:
            return 0
        bad = [b for b in bars if (b.adjust or "raw") != "raw"]
        if bad:
            raise ValueError(
                f"upsert_raw_bars 收到非 raw 口径 {len(bad)} 根"
                f"（adjust={bad[0].adjust!r}）——口径混用是 2026-10-27 事故类，拒写")
        cd = checked_dates or set()
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO daily_bars_raw
                   (symbol, timestamp, open, high, low, close, volume,
                    amount, turn, adjust, source, checked)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'raw', ?, ?)""",
                [
                    (b.symbol, b.timestamp.isoformat(), b.open, b.high, b.low,
                     b.close, b.volume, b.amount, b.turn, source,
                     1 if (checked or str(b.timestamp)[:10] in cd) else 0)
                    for b in bars
                ],
            )
        return len(bars)

    def get_raw_bars(self, symbol: str, start: Optional[str] = None,
                     end: Optional[str] = None,
                     limit: Optional[int] = None) -> list[Bar]:
        """原始价日线（时间升序）。与 `get_bars` 同语义，只是读 raw 表。"""
        sql = "SELECT * FROM daily_bars_raw WHERE symbol = ?"
        params: list = [symbol]
        if start:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end:
            sql += " AND timestamp <= ?"
            params.append(end)
        if limit:
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(int(limit))
        else:
            sql += " ORDER BY timestamp ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        bars = [
            Bar(symbol=r["symbol"], timestamp=datetime.fromisoformat(r["timestamp"]),
                open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                volume=r["volume"], turn=r["turn"] or 0.0, amount=r["amount"],
                adjust=r["adjust"])
            for r in rows
        ]
        if limit:
            bars.reverse()
        return bars

    def raw_latest(self, symbol: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM daily_bars_raw WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def raw_latest_checked(self, symbol: str) -> Optional[str]:
        """最近一次**经独立源同日对照过**的 raw bar 日期（无则 None）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM daily_bars_raw WHERE symbol = ? AND checked = 1",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def raw_date_flags(self, symbol: str, since: Optional[str] = None,
                       until: Optional[str] = None) -> dict:
        """`{YYYY-MM-DD: checked}`——**逐日**的验料台账（T41 逐日闸的数据底座）。

        只看 `MAX(timestamp) WHERE checked=1`（`raw_latest_checked`）会漏掉
        "中段有一堆日子从来没验过料"这种形状：那类行既不改变最新日期，
        也不改变最后 checked 日期，于是门禁看着是绿的（report-t39 §6-B 的盲区）。
        """
        sql = "SELECT substr(timestamp, 1, 10) d, checked FROM daily_bars_raw WHERE symbol = ?"
        params: list = [symbol]
        if since:
            sql += " AND substr(timestamp, 1, 10) >= ?"
            params.append(str(since)[:10])
        if until:
            sql += " AND substr(timestamp, 1, 10) <= ?"
            params.append(str(until)[:10])
        sql += " ORDER BY d"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {str(r["d"])[:10]: int(r["checked"] or 0) for r in rows}

    def raw_row_flags(self, symbol: str) -> dict:
        """单标的 raw 数据的口径自检结论（供 `data raw-coverage` 与引擎闸）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT timestamp, open, high, low, close, volume, amount, checked
                   FROM daily_bars_raw WHERE symbol = ? ORDER BY timestamp""",
                (symbol,),
            ).fetchall()
        out = {"symbol": symbol, "bars": len(rows), "last": None, "last_checked": None,
               "missing_amount": 0, "non_positive": 0, "ohlc_inconsistent": 0}
        if not rows:
            return out
        out["last"] = rows[-1]["timestamp"]
        ck = [r["timestamp"] for r in rows if r["checked"]]
        out["last_checked"] = ck[-1] if ck else None
        for r in rows:
            if not all(r[c] and r[c] > 0 for c in ("open", "high", "low", "close")):
                out["non_positive"] += 1
            if not (r["high"] >= max(r["open"], r["close"]) >= min(r["open"], r["close"]) >= r["low"]):
                out["ohlc_inconsistent"] += 1
            if not r["amount"]:
                out["missing_amount"] += 1
        return out

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

    # ---------- 分钟线与实时快照（盘中量化用） ----------

    def upsert_minute_bars(self, symbol: str, rows: list[dict],
                           period: str = "5") -> int:
        """写入分钟线（rows: [{ts, open, high, low, close, volume, amount}]）。

        口径 = **原始价**（T26/T31 同步）：新浪 N 分钟接口给的是交易所实时价，
        不做复权；实测 5 分钟最后一根收盘与当日 `daily_bars_raw.close` 一致，
        所以这里显式打 `adjust='raw'`，让引擎侧的口径闸也能覆盖分钟线。
        `period` 让 1 分钟与 5 分钟各行其道（T26：回测升频与保留窗口都要按周期筛）。
        """
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO minute_bars
                   (symbol, ts, open, high, low, close, volume, amount, adjust, period)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'raw', ?)
                   ON CONFLICT(symbol, ts, period) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume,
                     amount=excluded.amount, adjust='raw'""",
                [(symbol, r["ts"], float(r["open"]), float(r["high"]),
                  float(r["low"]), float(r["close"]),
                  float(r.get("volume") or 0), float(r.get("amount") or 0), str(period))
                 for r in rows])
        return len(rows)

    def get_minute_bars(self, symbol: str, limit: int = 240,
                        period: Optional[str] = None) -> list[dict]:
        """最近 N 根分钟线（按时间升序返回）。`period` 指定周期，默认不过滤。"""
        sql = ("SELECT * FROM (SELECT ts, open, high, low, close, volume, amount, adjust, period"
               " FROM minute_bars WHERE symbol = ?")
        params: list = [symbol]
        if period:
            sql += " AND period = ?"
            params.append(str(period))
        sql += " ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def minute_days(self, symbol: Optional[str] = None, period: str = "5") -> list[str]:
        """已入库的交易日（升序）——缺口/覆盖率判断的底。"""
        sql = "SELECT DISTINCT substr(ts,1,10) AS d FROM minute_bars WHERE period = ?"
        params: list = [str(period)]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        with self._connect() as conn:
            return [r["d"] for r in conn.execute(sql + " ORDER BY d", params)]

    def minute_ts_on(self, symbol: str, day: str, period: str = "5") -> set:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT ts FROM minute_bars
                   WHERE symbol = ? AND substr(ts,1,10) = ? AND period = ?""",
                (symbol, day, str(period))).fetchall()
        return {r["ts"] for r in rows}

    def minute_coverage(self, period: str = "5") -> dict:
        """存量概览（`minute stat` 用）。"""
        with self._connect() as conn:
            n = conn.execute("SELECT COUNT(*) AS c, COUNT(DISTINCT symbol) AS s,"
                             " MIN(ts) AS a, MAX(ts) AS b FROM minute_bars WHERE period = ?",
                             (str(period),)).fetchone()
        return {"period": str(period), "rows": int(n["c"] or 0),
                "symbols": int(n["s"] or 0), "first": n["a"], "last": n["b"]}

    def prune_minute_bars(self, before_day: str, period: Optional[str] = None) -> int:
        """保留窗口：删掉 `before_day`（不含）之前的分钟线，防磁盘单向涨满。"""
        sql = "DELETE FROM minute_bars WHERE substr(ts,1,10) < ?"
        params: list = [before_day]
        if period:
            sql += " AND period = ?"
            params.append(str(period))
        with self._connect() as conn:
            cur = conn.execute(sql, params)
        return int(cur.rowcount or 0)

    def minute_count(self, symbol: str) -> int:
        with self._connect() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM minute_bars WHERE symbol = ?",
                (symbol,)).fetchone()[0])

    def log_quote(self, ts: str, quotes: dict) -> int:
        """落盘实时快照（盘中盯市/复盘用）。ts 为 ISO 字符串。"""
        if not quotes:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO quote_log (ts, symbol, price, prev_close, volume)
                   VALUES (?, ?, ?, ?, ?)""",
                [(ts, sym, float(q.get("price") or 0), float(q.get("prev_close") or 0),
                  float(q.get("volume") or 0))
                 for sym, q in quotes.items() if isinstance(q, dict)])
        return len(quotes)

    def get_quote_history(self, symbol: str, day: str,
                          limit: int = 5000) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, price, prev_close, volume FROM quote_log"
                " WHERE symbol = ? AND substr(ts, 1, 10) = ? ORDER BY ts",
                (symbol, day)).fetchall()
        return [dict(r) for r in rows]

    def get_data_asof(self) -> Optional[str]:
        """全库最新 K 线日期（YYYY-MM-DD），无数据返回 None。"""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(timestamp) FROM daily_bars").fetchone()
        if not row or not row[0]:
            return None
        return str(row[0])[:10]
