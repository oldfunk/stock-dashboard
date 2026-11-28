"""收盘检阅的事实底座（F 线，2026-11-28）。

只读母库 + 纸盘只读桥；事实与解释分层，所有事实带 asof/来源。
不调用 LLM，不写库——纯采集与格式化，便于单测。
"""
from __future__ import annotations

import json
from typing import Optional

INDICES = (
    ("sh000300", "沪深300"),
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000922", "中证红利"),
)
ROLE_ORDER = ("持仓", "观察池", "钉选", "候选")


def pct(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev in (None, 0):
        return None
    return cur / prev - 1.0


def breadth_from_pairs(pairs: list[tuple[float, float]]) -> dict:
    """(今日收盘, 昨日收盘) → 涨/跌/平/总数。缺昨收的样本直接剔除。"""
    up = down = flat = 0
    for cur, prev in pairs:
        if cur is None or prev in (None, 0):
            continue
        if cur > prev:
            up += 1
        elif cur < prev:
            down += 1
        else:
            flat += 1
    total = up + down + flat
    return {"up": up, "down": down, "flat": flat, "total": total,
            "up_pct": (up / total) if total else None}


def _prev_trade_date(conn, date: str) -> Optional[str]:
    row = conn.execute(
        "SELECT MAX(trade_date) d FROM daily_close WHERE trade_date < ?",
        (date,)).fetchone()
    return row["d"] if row else None


def _close_on(conn, code: str, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT close FROM benchmark_daily WHERE code = ? AND trade_date = ?",
        (code, date)).fetchone()
    return float(row["close"]) if row and row["close"] is not None else None


def _close_before(conn, code: str, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT close FROM benchmark_daily WHERE code = ? AND trade_date < ? "
        "ORDER BY trade_date DESC LIMIT 1", (code, date)).fetchone()
    return float(row["close"]) if row and row["close"] is not None else None


def market_facts(conn, date: str) -> dict:
    """指数、成交额、涨跌家数；日期缺失时回退到 <= date 的最近交易日。"""
    row = conn.execute(
        "SELECT MAX(trade_date) d FROM daily_close WHERE trade_date <= ?",
        (date,)).fetchone()
    d = row["d"] if row else date
    prev = _prev_trade_date(conn, d) if d else None

    indices = []
    for code, name in INDICES:
        cur, before = _close_on(conn, code, d), _close_before(conn, code, d)
        indices.append({"code": code, "name": name, "close": cur,
                        "pct": pct(cur, before)})

    turnover = None
    if prev:
        cur_amt = conn.execute(
            "SELECT SUM(amount) s FROM daily_close WHERE trade_date = ?",
            (d,)).fetchone()["s"]
        prev_amt = conn.execute(
            "SELECT SUM(amount) s FROM daily_close WHERE trade_date = ?",
            (prev,)).fetchone()["s"]
        # daily_close.amount 单位是万元；换算成亿元（/1e4）
        turnover = {
            "cur_yi": round((cur_amt or 0) / 1e4, 2),
            "prev_yi": round((prev_amt or 0) / 1e4, 2),
            "pct": pct(cur_amt, prev_amt),
        }

    breadth = {"up": 0, "down": 0, "flat": 0, "total": 0, "up_pct": None}
    if prev:
        rows = conn.execute(
            "SELECT d.close AS cur, p.close AS prev FROM daily_close d "
            "JOIN daily_close p ON p.code = d.code AND p.trade_date = ? "
            "WHERE d.trade_date = ?", (prev, d)).fetchall()
        breadth = breadth_from_pairs([(r["cur"], r["prev"]) for r in rows])

    return {"asof": d, "prev_date": prev, "indices": indices,
            "turnover": turnover, "breadth": breadth}


def _price_pair(conn, code: str, date: str) -> tuple[Optional[str], Optional[float],
                                                     Optional[float]]:
    """优先全市场 daily_close，缺失再退 kline_daily；返回 (末日, 今收, 昨收)。"""
    for table in ("daily_close", "kline_daily"):
        rows = conn.execute(
            f"SELECT trade_date, close FROM {table} WHERE code = ? "
            "AND trade_date <= ? ORDER BY trade_date DESC LIMIT 2",
            (code, date)).fetchall()
        if len(rows) >= 2:
            return rows[0]["trade_date"], rows[0]["close"], rows[1]["close"]
        if len(rows) == 1:
            return rows[0]["trade_date"], rows[0]["close"], None
    return None, None, None


def target_universe(conn) -> dict[str, dict]:
    """持仓/观察池/钉选/候选，按相关性合并；同代码取最高优先级角色。"""
    out: dict[str, dict] = {}

    def add(code, name, role):
        c = str(code or "").zfill(6)
        if not c or not c.isdigit():
            return
        item = out.setdefault(c, {"code": c, "name": str(name or ""),
                                  "roles": []})
        if name and not item["name"]:
            item["name"] = str(name)
        if role not in item["roles"]:
            item["roles"].append(role)

    try:
        from src.paper_bridge import load_positions
        for p in (load_positions().get("data") or []):
            if int(p.get("total_volume") or 0) > 0:
                add(p.get("symbol"), p.get("name"), "持仓")
    except Exception:
        pass
    try:
        from src.models.ai_watchlist import AiWatchlistDAO
        for w in AiWatchlistDAO().get_all():
            add(w.get("code"), w.get("name"), "观察池")
    except Exception:
        pass
    try:
        from src.models.database import WatchlistDAO
        for w in WatchlistDAO().list_all():
            add(w.get("code"), w.get("name"), "钉选")
    except Exception:
        pass
    try:
        from src.models.database import ScreeningResultDAO
        for c in ScreeningResultDAO().get_latest_results(limit=20):
            add(c.get("code"), c.get("name"), "候选")
    except Exception:
        pass
    return out


def target_facts(conn, date: str) -> list[dict]:
    universe = target_universe(conn)
    codes = list(universe)
    thesis = {}
    if codes:
        from src.models.database import WatchlistThesisDAO
        thesis = WatchlistThesisDAO.get_many(codes)

    out = []
    for code, item in universe.items():
        snap = conn.execute(
            "SELECT name, sector, pe, pb, roe FROM stock_snapshot WHERE code = ?",
            (code,)).fetchone()
        last_date, cur, prev = _price_pair(conn, code, date)
        th = thesis.get(code) or {}
        out.append({
            "code": code,
            "name": (snap["name"] if snap and snap["name"] else item["name"]),
            "roles": item["roles"],
            "sector": snap["sector"] if snap else None,
            "pe": snap["pe"] if snap else None,
            "pb": snap["pb"] if snap else None,
            "roe": snap["roe"] if snap else None,
            "last_date": last_date,
            "close": cur,
            "pct": pct(cur, prev),
            "thesis": (th.get("core_thesis") or "")[:300],
            "red_lines": [r.get("condition") for r in (th.get("red_lines") or [])
                          if isinstance(r, dict)][:5],
            "assumptions": [a.get("content") for a in (th.get("assumptions") or [])
                            if isinstance(a, dict)][:5],
        })
    return out


def portfolio_facts() -> dict:
    """纸盘变量（只读）；桥不可用时返回 available=False，不阻断笔记。"""
    try:
        from src.paper_bridge import load_status
        st = load_status(nav_limit=2, fills_limit=5)
    except Exception as e:
        return {"available": False, "reason": str(e)[:200]}
    navs = (st.get("nav") or {}).get("data") or []
    acc = (st.get("account") or {}).get("data") or {}
    pos = (st.get("positions") or {}).get("data") or []
    plan = (st.get("plan") or {}).get("data") or []
    fills = (st.get("fills") or {}).get("data") or []
    nav = navs[0] if navs else {}
    return {
        "available": bool(nav or acc),
        "nav": {k: nav.get(k) for k in
                ("timestamp", "cash", "market_value", "total_value", "pnl_pct")},
        "nav_note": "pnl_pct 为成立以来累计收益，不是本周收益",
        "cash": acc.get("cash"),
        "positions": [{k: p.get(k) for k in
                       ("symbol", "total_volume", "available_volume", "avg_cost")}
                      for p in pos],
        "pending_plan": ({k: plan.get(k) for k in
                          ("id", "plan_date", "created_at")} if plan else None),
        "fills": [{k: f.get(k) for k in
                   ("timestamp", "symbol", "direction", "volume", "price",
                    "commission", "stamp_duty", "transfer_fee")}
                  for f in fills],
    }


def facts_for_prompt(facts: dict) -> str:
    return json.dumps(facts, ensure_ascii=False, default=str)
