"""条件单：AI（或人）下达"触发条件"，引擎在盘中盯价自动执行。

与普通委托的区别：现在不成交，只**挂条件**。满足触发价时由引擎按
同一套风控与撮合口径下单（fail-closed 复用 RiskManager/PaperBroker）。

状态机：armed → filled / cancelled / expired / rejected
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

CONDITION_TYPES = ("price_below", "price_above", "pct_below", "pct_above")

STATUSES = ("armed", "filled", "cancelled", "expired", "rejected")


class ConditionalOrders:
    """条件单仓库（与账本同库；状态可审计、可恢复）。"""

    def __init__(self, db_path: str | Path = "paper_account.db"):
        self.db_path = Path(db_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conditional_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    day TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    cond_type TEXT NOT NULL,
                    cond_value REAL NOT NULL,
                    volume INTEGER DEFAULT 0,
                    value REAL DEFAULT 0,
                    expiry TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'armed',
                    note TEXT DEFAULT '',
                    filled_at TEXT,
                    filled_price REAL,
                    detail TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cond_status
                ON conditional_orders(status, expiry)
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

    # ---------- 校验与入库 ----------

    @staticmethod
    def validate(item: dict, now: Optional[datetime] = None,
                 min_ticket: float = 0.0) -> tuple[bool, str, dict]:
        """fail-closed 校验：非法条件单不入库。

        min_ticket > 0 时按手续费地板在**入库前**就拒掉过小的买入金额——
        与盘中执行用的是同一道门槛，不给"挂得上、永远触发不了"的单子留空间。
        """
        from paper_trading.integration.universe import is_a_share

        now = now or datetime.now()
        sym = str(item.get("symbol") or "").strip()
        side = str(item.get("side") or "").strip().lower()
        ctype = str(item.get("cond_type") or "").strip().lower()
        try:
            cval = float(item.get("cond_value"))
        except (TypeError, ValueError):
            return False, "触发价必须是数字", {}
        if len(sym) != 6 or not sym.isdigit():
            return False, f"股票代码非法：{sym}", {}
        if not is_a_share(sym):
            return False, f"非 A 股代码：{sym}", {}
        if side not in ("buy", "sell"):
            return False, f"方向须为 buy/sell：{side}", {}
        if ctype not in CONDITION_TYPES:
            return False, f"触发类型须为 {','.join(CONDITION_TYPES)}", {}
        if cval <= 0:
            return False, "触发值必须为正", {}
        volume = int(item.get("volume") or 0)
        value = float(item.get("value") or 0)
        if side == "buy":
            if volume % 100 != 0 or volume < 0:
                return False, "买入数量须为 100 的整数倍", {}
            if volume == 0 and value <= 0:
                return False, "买入须给 volume 或 value", {}
            if min_ticket > 0 and volume == 0 and value < float(min_ticket):
                return False, f"买入金额 {value:.0f} 元低于最小下单金额 {float(min_ticket):.0f} 元", {}
        if side == "sell" and volume % 100 != 0:
            return False, "卖出数量须为 100 的整数倍", {}
        exp = str(item.get("expiry") or "").strip()
        if not exp:
            exp = (now + timedelta(days=2)).date().isoformat()
        try:
            exp_dt = datetime.fromisoformat(exp[:19] if len(exp) > 10 else exp + " 23:59")
        except ValueError:
            return False, f"有效期格式非法：{exp}", {}
        if exp_dt <= now:
            return False, "有效期必须晚于当前时间", {}
        if (exp_dt - now).days > 30:
            return False, "有效期最长 30 天", {}
        clean = {"symbol": sym, "side": side, "cond_type": ctype,
                 "cond_value": cval, "volume": volume, "value": value,
                 "expiry": exp_dt.isoformat(timespec="minutes"),
                 "note": str(item.get("note") or "")[:200],
                 "source": str(item.get("source") or "manual")[:20]}
        return True, "ok", clean

    def create(self, item: dict, now: Optional[datetime] = None,
               min_ticket: float = 0.0) -> tuple[bool, str, int]:
        ok, msg, clean = self.validate(item, now, min_ticket)
        if not ok:
            return False, msg, 0
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO conditional_orders
                   (created_at, day, source, symbol, side, cond_type, cond_value,
                    volume, value, expiry, status, note, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'armed', ?, ?)""",
                ((now or datetime.now()).isoformat(timespec="seconds"),
                 (now or datetime.now()).date().isoformat(), clean["source"],
                 clean["symbol"], clean["side"], clean["cond_type"],
                 clean["cond_value"], clean["volume"], clean["value"],
                 clean["expiry"], clean["note"],
                 json.dumps({"raw": item}, ensure_ascii=False, default=str)))
            return True, f"已挂条件单 #{cur.lastrowid}", int(cur.lastrowid)

    def create_many(self, items: list, now: Optional[datetime] = None,
                    min_ticket: float = 0.0) -> dict:
        made, rejected = [], []
        for it in items or []:
            ok, msg, oid = self.create(it, now, min_ticket)
            (made if ok else rejected).append(
                {"id": oid, "symbol": it.get("symbol"), "why": None if ok else msg})
        return {"created": made, "rejected": rejected,
                "n_created": len(made), "n_rejected": len(rejected)}

    # ---------- 查询/状态 ----------

    def armed(self, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conditional_orders WHERE status = 'armed'"
                " ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                if datetime.fromisoformat(d["expiry"]) <= now:
                    d["_expired"] = True
            except Exception:
                d["_expired"] = False
            out.append(d)
        return out

    def pending(self, now: Optional[datetime] = None) -> list[dict]:
        """armed 且未过期的。"""
        return [d for d in self.armed(now) if not d.get("_expired")]

    def mark(self, oid: int, status: str, price: Optional[float] = None,
             detail: Optional[dict] = None, now: Optional[datetime] = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"非法状态：{status}")
        now = now or datetime.now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE conditional_orders SET status = ?,"
                " filled_at = CASE WHEN ? = 'filled' THEN ? ELSE filled_at END,"
                " filled_price = COALESCE(?, filled_price),"
                " detail = COALESCE(?, detail) WHERE id = ?",
                (status, status, now.isoformat(timespec="seconds"), price,
                 json.dumps(detail, ensure_ascii=False, default=str)
                 if detail else None, int(oid)))

    def expire_due(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE conditional_orders SET status = 'expired',"
                " detail = COALESCE(detail, '{\"why\":\"有效期已过\"}')"
                " WHERE status = 'armed' AND expiry <= ?",
                (now.isoformat(timespec="minutes"),))
            return cur.rowcount or 0

    def list_all(self, limit: int = 100, status: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM conditional_orders"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def cancel(self, oid: int, why: str = "人工撤销") -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE conditional_orders SET status='cancelled',"
                " detail=? WHERE id=? AND status='armed'", (why, int(oid)))
            return bool(cur.rowcount)


def triggered(cond: dict, price: float, change_pct: float = 0.0) -> bool:
    """条件是否被实时价触发（纯函数，回测/实盘共用）。"""
    t = str(cond.get("cond_type") or "")
    try:
        v = float(cond.get("cond_value"))
    except (TypeError, ValueError):
        return False
    if price <= 0:
        return False
    if t == "price_below":
        return price <= v
    if t == "price_above":
        return price >= v
    if t == "pct_below":
        return change_pct <= -abs(v)
    if t == "pct_above":
        return change_pct >= abs(v)
    return False