"""纸盘状态的只读装载（E0）。

所有函数返回统一形状：

    {"source": "paper:account" | "unavailable",
     "asof": "YYYY-MM-DD..." | None,
     "stale": bool,
     "data": ... | None,
     "reason": "..." (仅 unavailable/缺数据时)}

失败一律降级，不抛异常：桥不能拖住 15:30 流水线或 AI 分析。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from .conn import connect
from .settings import bridge_paths


def _payload(source: str, data: Any, asof: str | None,
             max_age_days: int = 5) -> dict:
    stale = False
    if asof:
        try:
            age = (date.today()
                   - datetime.fromisoformat(str(asof)[:19]).date()).days
            stale = age > max_age_days
        except ValueError:
            stale = False
    return {"source": source, "asof": (str(asof)[:19] if asof else None),
            "stale": stale, "data": data}


def _unavailable(reason: str) -> dict:
    return {"source": "unavailable", "asof": None, "stale": False,
            "data": None, "reason": str(reason)[:200]}


def _open(data_db=None, account_db=None):
    _, acct = bridge_paths(data_db, account_db)
    return connect(acct)


def load_account(data_db=None, account_db=None) -> dict:
    conn = _open(data_db, account_db)
    if conn is None:
        return _unavailable("纸盘账户库不存在或不可读")
    try:
        row = conn.execute(
            "SELECT cash, initial_cash FROM account ORDER BY id LIMIT 1"
        ).fetchone()
    except sqlite3.Error as e:
        return _unavailable(f"account 读取失败：{e}")
    finally:
        conn.close()
    if not row:
        return _unavailable("account 无数据行")
    return _payload("paper:account", dict(row), None)


def load_positions(data_db=None, account_db=None) -> dict:
    conn = _open(data_db, account_db)
    if conn is None:
        return _unavailable("纸盘账户库不存在或不可读")
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, total_volume, available_volume, avg_cost, "
            "last_update, peak_price FROM positions ORDER BY symbol")]
    except sqlite3.Error as e:
        return _unavailable(f"positions 读取失败：{e}")
    finally:
        conn.close()
    asof = max((r.get("last_update") for r in rows if r.get("last_update")),
               default=None)
    return _payload("paper:positions", rows, asof)


def load_pending_plan(day: str | None = None, data_db=None,
                      account_db=None) -> dict:
    day = day or date.today().isoformat()
    conn = _open(data_db, account_db)
    if conn is None:
        return _unavailable("纸盘账户库不存在或不可读")
    try:
        row = conn.execute(
            "SELECT id, plan_date, symbols, plan_json, status, created_at, "
            "executed_at FROM agent_plans WHERE plan_date = ? AND status = "
            "'pending' ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    except sqlite3.Error as e:
        return _unavailable(f"agent_plans 读取失败：{e}")
    finally:
        conn.close()
    return _plan_payload(row)


def load_latest_pending_plan(data_db=None, account_db=None) -> dict:
    """最近一条 pending 计划（不要求 plan_date=今天；面板摘要用）。"""
    conn = _open(data_db, account_db)
    if conn is None:
        return _unavailable("纸盘账户库不存在或不可读")
    try:
        row = conn.execute(
            "SELECT id, plan_date, symbols, plan_json, status, created_at, "
            "executed_at FROM agent_plans WHERE status = 'pending' "
            "ORDER BY plan_date DESC, id DESC LIMIT 1").fetchone()
    except sqlite3.Error as e:
        return _unavailable(f"agent_plans 读取失败：{e}")
    finally:
        conn.close()
    return _plan_payload(row)


def _plan_payload(row) -> dict:
    if not row:
        return _payload("paper:plans", None, None)
    data = dict(row)
    try:
        data["plan"] = json.loads(data.get("plan_json") or "{}")
    except ValueError:
        data["plan"] = {}
    data.pop("plan_json", None)
    return _payload("paper:plans", data, data.get("created_at"))


def load_recent_fills(limit: int = 20, data_db=None,
                      account_db=None) -> dict:
    conn = _open(data_db, account_db)
    if conn is None:
        return _unavailable("纸盘账户库不存在或不可读")
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT fill_id, order_id, symbol, direction, volume, price, "
            "commission, stamp_duty, transfer_fee, timestamp FROM fills "
            "ORDER BY fill_id DESC LIMIT ?", (int(limit),))]
    except sqlite3.Error as e:
        return _unavailable(f"fills 读取失败：{e}")
    finally:
        conn.close()
    asof = rows[0]["timestamp"] if rows else None
    return _payload("paper:fills", rows, asof)


def load_nav(limit: int = 30, data_db=None, account_db=None) -> dict:
    conn = _open(data_db, account_db)
    if conn is None:
        return _unavailable("纸盘账户库不存在或不可读")
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT timestamp, cash, market_value, total_value, pnl, pnl_pct "
            "FROM nav_history ORDER BY timestamp DESC LIMIT ?", (int(limit),))]
    except sqlite3.Error as e:
        return _unavailable(f"nav_history 读取失败：{e}")
    finally:
        conn.close()
    asof = rows[0]["timestamp"] if rows else None
    return _payload("paper:nav", rows, asof)


def load_status(nav_limit: int = 5, fills_limit: int = 5) -> dict:
    """面板/摘要用的一组状态（每块都带 source/asof/stale）。"""
    out = {
        "account": load_account(),
        "positions": load_positions(),
        "plan": load_latest_pending_plan(),
        "nav": load_nav(limit=nav_limit),
    }
    if fills_limit:
        out["fills"] = load_recent_fills(limit=fills_limit)
    return out
