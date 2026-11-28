"""日/周报（A5）：持仓、成交、归因、风控事件、健康度变化。

只读：所有数据来自账本 `nav_history`/`fills`/`op_log` 与只读健康度，
不写账本、不改状态、不调用 LLM。口径边界写在每个字段的 `basis` 里：

- `nav`：账本收盘净值（唯一口径）；
- `trades`：`fills` 原始成交（含费用），不是意图/委托；
- `attribution`：按标的汇总的**成交现金**与**持仓浮动盈亏（成本口径）**，
  不是逐日归因，也不把未实现盈亏说成当日收益；
- `benchmark`：母库基准；不可用时如实 `available=false`，不补零；
- `health_change`：用 `health_payload(as_of=...)` 在期初/期末各算一次，
  窗口滚动，所以“变化”是窗口口径变化，不宣称是单日因果。

CLI:
    python -m paper_trading.cli report daily --date 2026-11-27
    python -m paper_trading.cli report weekly --end 2026-11-27 --days 7
    python -m paper_trading.cli report ... --out report.json
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional


def _ro(path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(account_db, sql: str, args: tuple = ()) -> list[dict]:
    conn = _ro(account_db)
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _fills(account_db, start: str, end: str) -> list[dict]:
    return _rows(
        account_db,
        "SELECT * FROM fills WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?"
        " ORDER BY fill_id", (start, end))


def _navs(account_db, start: str, end: str) -> list[dict]:
    return _rows(
        account_db,
        "SELECT * FROM nav_history WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?"
        " ORDER BY id", (start, end))


def _ops(account_db, start: str, end: str) -> list[dict]:
    return _rows(
        account_db,
        "SELECT * FROM op_log WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?"
        " ORDER BY id DESC", (start, end))


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _family(action: str) -> str:
    a = str(action or "")
    if a.startswith("risk:"):
        return "risk"
    if a.startswith("ai:"):
        return "ai"
    if a.startswith("engine:") or a.startswith("cond:"):
        return "execution"
    if a.startswith("data:") or a.startswith("nav:"):
        return "data"
    return "other"


def _benchmark_return(bench_code: str, days: list[str],
                      start: str, end: str) -> dict:
    from paper_trading.integration import mother as m

    try:
        blk = (m.benchmark([bench_code], days=max(len(days) * 2, 120)) or {}
               ).get(bench_code) or {}
    except Exception as exc:                    # noqa: BLE001
        return {"available": False, "code": bench_code,
                "reason": f"{type(exc).__name__}: {exc}"}
    if not blk.get("available"):
        return {"available": False, "code": bench_code,
                "reason": blk.get("note") or "母库 benchmark_daily 不可用"}
    closes = {}
    for r in blk.get("data") or []:
        try:
            closes[str(r["date"])[:10]] = float(r["close"])
        except (KeyError, TypeError, ValueError):
            continue
    if start == end:
        # 日报：基准也要和上一个基准交易日比，否则区间只有一天
        e = end if end in closes else max(
            (d for d in closes if d <= end), default=None)
        prev = [d for d in sorted(closes) if d < start]
        s = prev[-1] if prev else None
    else:
        inrange = sorted(d for d in closes if start <= d <= end)
        if len(inrange) < 2:
            return {"available": False, "code": bench_code,
                    "reason": f"区间内基准交易日不足（{len(inrange)}）"}
        s, e = inrange[0], inrange[-1]
    if not s or not e or s == e:
        return {"available": False, "code": bench_code,
                "reason": "基准在区间内没有可比的前后交易日"}
    return {"available": True, "code": bench_code, "start": s, "end": e,
            "return": round(closes[e] / closes[s] - 1, 6),
            "asof": blk.get("asof"), "stale": bool(blk.get("stale"))}


def _health_at(account_db, day: Optional[str]) -> dict:
    from paper_trading.strategy.health import health_payload

    try:
        h = health_payload(account_db, as_of=day) if day else health_payload(account_db)
    except Exception as exc:                    # noqa: BLE001
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    keep = ("available", "health", "recommendation", "reasons", "window",
            "metrics", "stale", "reason", "asof")
    return {k: h.get(k) for k in keep if k in h}


def _health_change(account_db, start: str, end: str) -> dict:
    before_day = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    before = _health_at(account_db, before_day)
    after = _health_at(account_db, end)
    delta = None
    try:
        if before.get("available") and after.get("available"):
            delta = round(_num(after["metrics"].get("excess_20"))
                          - _num(before["metrics"].get("excess_20")), 6)
    except (KeyError, TypeError, ValueError):
        delta = None
    changed = (before.get("health") != after.get("health"))
    return {"before": before, "after": after,
            "health_changed": changed, "excess_20_delta": delta,
            "basis": "窗口滚动口径；before=期初前一交易日，after=期末"}


def _attribution(fills: list[dict], positions: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for f in fills:
        sym = str(f.get("symbol") or "")
        rec = by.setdefault(sym, {"symbol": sym, "buy_amount": 0.0,
                                  "sell_amount": 0.0, "buy_volume": 0,
                                  "sell_volume": 0, "fees": 0.0,
                                  "n_fills": 0})
        amt = _num(f.get("price")) * _num(f.get("volume"))
        fees = sum(_num(f.get(k)) for k in
                   ("commission", "stamp_duty", "transfer_fee"))
        rec["fees"] += fees
        rec["n_fills"] += 1
        if int(_num(f.get("direction"))) == 1:
            rec["buy_amount"] += amt
            rec["buy_volume"] += int(_num(f.get("volume")))
        else:
            rec["sell_amount"] += amt
            rec["sell_volume"] += int(_num(f.get("volume")))
    pos_by = {str(p.get("symbol")): p for p in positions}
    out = []
    for sym, rec in by.items():
        rec["net_cash"] = round(rec["sell_amount"] - rec["buy_amount"]
                                - rec["fees"], 2)
        rec["buy_amount"] = round(rec["buy_amount"], 2)
        rec["sell_amount"] = round(rec["sell_amount"], 2)
        rec["fees"] = round(rec["fees"], 2)
        p = pos_by.get(sym)
        if p:
            rec["position"] = {
                "total": p.get("total_volume"),
                "available": p.get("available_volume"),
                "avg_cost": p.get("avg_cost"),
                "price": p.get("current_price"),
                "unrealized_pnl": round(
                    (_num(p.get("current_price")) - _num(p.get("avg_cost")))
                    * _num(p.get("total_volume")), 2),
                "basis": "持仓浮动盈亏（成本口径），不等于本区间收益",
            }
        out.append(rec)
    out.sort(key=lambda r: -(r["buy_amount"] + r["sell_amount"]))
    return out


def _sector_attribution(positions: list[dict]) -> dict:
    """按持仓市值聚合行业；母库不可用时显式 unavailable，不填 0。"""
    if not positions:
        return {"available": False, "reason": "无持仓", "items": [],
                "total_market_value": 0.0}
    from paper_trading.integration import mother as m

    syms = [str(p.get("symbol")) for p in positions if p.get("symbol")]
    try:
        blk = m.sectors(syms)
    except Exception as exc:                    # noqa: BLE001
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}",
                "items": [], "total_market_value": 0.0}
    if not blk.get("available"):
        return {"available": False, "reason": blk.get("note") or "行业数据不可用",
                "items": [], "total_market_value": 0.0}
    data = blk.get("data") or {}
    agg: dict[str, float] = {}
    total = 0.0
    known = 0
    for p in positions:
        sym = str(p.get("symbol") or "")
        mv = _num(p.get("market_value"))
        total += mv
        sec = data.get(sym)
        if sec:
            agg[str(sec)] = agg.get(str(sec), 0.0) + mv
            known += 1
        else:
            agg["未知"] = agg.get("未知", 0.0) + mv
    items = [{"sector": k, "market_value": round(v, 2),
              "weight": round(v / total, 6) if total else None}
             for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]
    return {"available": True, "source": blk.get("source"),
            "asof": blk.get("asof"), "stale": bool(blk.get("stale")),
            "items": items, "total_market_value": round(total, 2),
            "known": known, "unknown": len(positions) - known,
            "basis": "持仓市值按 stock_snapshot.sector 聚合；NULL 归未知"}


def build_report(bridge, start: str, end: str, *, label: str = "",
                 benchmark: str = "sh000300") -> dict:
    account_db = bridge.account_db_path
    navs = _navs(account_db, start, end)
    fills = _fills(account_db, start, end)
    ops = _ops(account_db, start, end)
    # 每日取最后一条 NAV（同一天可能有多条盘中快照）
    last_by_day: dict[str, dict] = {}
    for r in navs:
        last_by_day[str(r["timestamp"])[:10]] = r
    days = sorted(last_by_day)
    nav_end = _num(last_by_day[days[-1]]["total_value"]) if days else None
    nav_start = _num(last_by_day[days[0]]["total_value"]) if days else None
    prev_day = None
    if start == end and days:
        # 日报：与前一个有净值的交易日比，才叫“当日变化”
        prev = _rows(account_db,
                     "SELECT substr(timestamp, 1, 10) AS d, total_value"
                     " FROM nav_history WHERE substr(timestamp, 1, 10) < ?"
                     " ORDER BY timestamp DESC, id DESC LIMIT 1", (start,))
        if prev:
            prev_day = prev[0]["d"]
            nav_start = _num(prev[0]["total_value"])
    nav_change = (round(nav_end / nav_start - 1, 6)
                  if nav_start and nav_end else None)
    bench = _benchmark_return(benchmark, days, start, end)
    excess = (round(nav_change - bench["return"], 6)
              if nav_change is not None and bench.get("available") else None)

    try:
        status = bridge.get_status()
    except Exception as exc:                    # noqa: BLE001
        status = {"positions": [], "error": f"{type(exc).__name__}: {exc}"}
    positions = status.get("positions") or []
    risk_events = []
    for o in ops:
        if _family(str(o.get("action"))) != "risk":
            continue
        risk_events.append({
            "time": o.get("timestamp"), "action": o.get("action"),
            "ok": bool(o.get("ok")), "params": o.get("params"),
            "result": o.get("result")})
    try:
        risk_now = bridge.risk.describe(_num(status.get("total_value")))
    except Exception as exc:                    # noqa: BLE001
        risk_now = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    health = _health_change(account_db, start, end)
    report = {
        "period": {"start": start, "end": end,
                   "label": label or ("daily" if start == end else "range"),
                   "trading_days": days},
        "nav": {
            "start": nav_start, "end": nav_end, "change": nav_change,
            "previous_day": prev_day,
            "change_basis": ("当日 vs 上一净值日" if prev_day
                             else "区间首末（区间内无更早净值）"),
            "daily": [{"date": d, "total_value": _num(last_by_day[d]["total_value"]),
                       "cash": _num(last_by_day[d]["cash"]),
                       "market_value": _num(last_by_day[d]["market_value"])}
                      for d in days],
            "basis": "nav_history 每日最后一条（与面板/健康度同口径）",
        },
        "benchmark": bench,
        "excess_return": excess,
        "trades": [{
            "time": f.get("timestamp"), "symbol": f.get("symbol"),
            "direction": "buy" if int(_num(f.get("direction"))) == 1 else "sell",
            "volume": f.get("volume"), "price": f.get("price"),
            "fees": round(sum(_num(f.get(k)) for k in
                              ("commission", "stamp_duty", "transfer_fee")), 2),
        } for f in fills],
        "attribution": _attribution(fills, positions),
        "sector_attribution": _sector_attribution(positions),
        "positions": positions,
        "risk": {"current": risk_now, "events": risk_events},
        "health_change": health,
        "health": health.get("after"),
        "operations": {
            "n": len(ops),
            "by_family": {k: sum(1 for o in ops if _family(str(o.get("action"))) == k)
                          for k in ("ai", "risk", "execution", "data", "other")},
            "failed": [{"time": o.get("timestamp"), "action": o.get("action"),
                        "result": o.get("result")}
                       for o in ops if not o.get("ok")][:20],
        },
        "note": "只读报告；归因只到标的级，不宣称单日因果。",
    }
    return report


def daily_report(bridge, day: str, *, benchmark: str = "sh000300") -> dict:
    return build_report(bridge, day, day, label="daily", benchmark=benchmark)


def weekly_report(bridge, end: str, days: int = 7,
                  *, benchmark: str = "sh000300") -> dict:
    days = max(1, min(int(days), 31))
    start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
    return build_report(bridge, start, end, label=f"weekly({days}d)",
                        benchmark=benchmark)


def write_report(report: dict, out: str | Path) -> str:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    return str(p)
