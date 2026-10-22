"""引擎运行态：日内预算、每标的冷却、规则触发历史（SQLite，与账本同库）。

为什么落库而不是内存：守护进程可能被重启/kill，落库保证节流与预算
不会因重启被绕过（fail-closed 的延续）。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator


class EngineState:
    def __init__(self, db_path: str | Path = "paper_account.db"):
        self.db_path = Path(db_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS engine_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS engine_triggers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    day TEXT NOT NULL,
                    rule_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    status TEXT,
                    detail TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_engine_trig_day
                ON engine_triggers(day)
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- 通用 KV ----------

    def get(self, key: str, default=None):
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM engine_state WHERE key = ?",
                               (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def set(self, key: str, value) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO engine_state (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False, default=str),
                 datetime.now().isoformat()))

    # ---------- 预算与冷却 ----------

    def budget(self, day: str | None = None) -> dict:
        day = day or date.today().isoformat()
        return self.get(f"budget:{day}", {"orders": 0, "turnover": 0.0})

    def bump_budget(self, day: str, orders: int = 1, turnover: float = 0.0) -> dict:
        b = self.budget(day)
        b["orders"] = int(b.get("orders", 0)) + orders
        b["turnover"] = float(b.get("turnover", 0.0)) + float(turnover)
        self.set(f"budget:{day}", b)
        return b

    def last_order_ts(self, symbol: str) -> str:
        return self.get(f"cooldown:{symbol}", "")

    def mark_order(self, symbol: str, ts: str) -> None:
        self.set(f"cooldown:{symbol}", ts)

    def fired_rules(self, day: str | None = None) -> set[str]:
        day = day or date.today().isoformat()
        rows = self.get(f"fired:{day}", []) or []
        return set(rows)

    def mark_fired(self, rule_id: str, day: str | None = None) -> None:
        day = day or date.today().isoformat()
        rows = self.get(f"fired:{day}", []) or []
        if rule_id not in rows:
            rows.append(rule_id)
        self.set(f"fired:{day}", rows)

    def clear_day(self, day: str | None = None) -> None:
        day = day or date.today().isoformat()
        for k in (f"budget:{day}", f"fired:{day}"):
            with self._connect() as conn:
                conn.execute("DELETE FROM engine_state WHERE key = ?", (k,))

    # ---------- 触发流水 ----------

    def log_trigger(self, ts: str, day: str, rule_id: str, symbol: str,
                    side: str, status: str, detail: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO engine_triggers (ts, day, rule_id, symbol, side,"
                " status, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, day, rule_id, symbol, side, status,
                 json.dumps(detail, ensure_ascii=False, default=str)))

    def log_trigger_once(self, key: str, ts: str, day: str, rule_id: str,
                         symbol: str, side: str, status: str, detail: dict) -> bool:
        """同一 key 每天只落一条（用于 enabled=false 拦截意图，防每分钟刷屏）。

        返回是否真的写入了。开关关着时意图会持续出现，但面板只需要看到
        “哪条规则/条件单本来要动”，不需要一分钟一条。
        """
        marker = f"trigger-once:{day}:{key}"
        if self.get(marker):
            return False
        self.log_trigger(ts, day, rule_id, symbol, side, status, detail)
        self.set(marker, ts)
        return True

    def triggers_today(self, day: str | None = None, limit: int = 50) -> list[dict]:
        day = day or date.today().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM engine_triggers WHERE day = ? ORDER BY id DESC LIMIT ?",
                (day, int(limit))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d.get("detail") or "{}")
            except Exception:
                d["detail"] = {}
            out.append(d)
        return out