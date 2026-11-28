"""R3-4 归因：纸盘周期收益/超额/费用/行业/个股/regime（只读账本）。

归因口径写死：
- total_return：周期内首末 NAV（同一部署）；
- excess_return：纸盘收益 - 基准收益（默认 sh000300）；
- costs：周期内成交费用（佣金+印花税+过户费）；
- stock/sector：周期内 FIFO 已实现盈亏（不含费用）；
- factor：成交笔数/买/卖/成交额/费用/已实现；
- regime：周期末最近的 market_state_daily.regime。
不预测、不调仓、不写纸盘账本。
"""
from __future__ import annotations

from typing import Optional

from src.models.database import db_conn
from src.utils import now_cn


def fifo_realized(fills: list[dict], start: str, end: str) -> dict[str, float]:
    """按 FIFO 算 [start,end] 内已实现盈亏（费用另计）。"""
    lots: dict[str, list[list[float]]] = {}
    realized: dict[str, float] = {}
    for f in sorted(fills, key=lambda x: str(x.get("timestamp") or "")):
        sym = str(f.get("symbol") or "").zfill(6)
        qty = int(f.get("volume") or 0)
        price = float(f.get("price") or 0)
        if not sym or qty <= 0 or price <= 0:
            continue
        day = str(f.get("timestamp") or "")[:10]
        if int(f.get("direction") or 0) > 0:
            lots.setdefault(sym, []).append([qty, price])
            continue
        # 卖出：FIFO 平仓；只把落在周期内的卖出计入归因
        left = qty
        pnl = 0.0
        book = lots.setdefault(sym, [])
        while left > 0 and book:
            lot = book[0]
            take = min(left, int(lot[0]))
            pnl += (price - float(lot[1])) * take
            lot[0] -= take
            left -= take
            if lot[0] <= 0:
                book.pop(0)
        if start <= day <= end and pnl:
            realized[sym] = realized.get(sym, 0.0) + pnl
    return realized


def _nav_return(navs: list[dict], start: str, end: str) -> tuple[Optional[float],
                                                                 Optional[float]]:
    rows = sorted((str(n.get("timestamp"))[:10], float(n.get("total_value") or 0))
                  for n in navs if n.get("total_value") is not None)
    in_range = [(d, v) for d, v in rows if start <= d <= end]
    if len(in_range) < 2:
        return None, None
    return in_range[0][1], in_range[-1][1]


def _bench_return(code: str, start: str, end: str) -> Optional[float]:
    with db_conn() as conn:
        a = conn.execute(
            "SELECT close FROM benchmark_daily WHERE code = ? AND trade_date >= ? "
            "ORDER BY trade_date LIMIT 1", (code, start)).fetchone()
        b = conn.execute(
            "SELECT close FROM benchmark_daily WHERE code = ? AND trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT 1", (code, end)).fetchone()
    if not a or not b or not a["close"] or not b["close"]:
        return None
    return float(b["close"]) / float(a["close"]) - 1.0


def _sector_map(codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT code, sector FROM stock_snapshot WHERE code IN (%s)"
            % ",".join("?" * len(codes)), codes).fetchall()
    return {str(r["code"]).zfill(6): (r["sector"] or "未知") for r in rows}


def _regime(end: str) -> str:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT regime FROM market_state_daily WHERE trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT 1", (end,)).fetchone()
    return (row["regime"] or "") if row else ""


def attribute_period(start: str, end: str, benchmark: str = "sh000300",
                     deployment_id: str = "") -> dict:
    from src.paper_bridge import load_nav, load_recent_fills

    nav = load_nav(limit=1000)
    if nav.get("source") == "unavailable":
        raise RuntimeError(f"纸盘 NAV 不可用：{nav.get('reason')}")
    fills_payload = load_recent_fills(limit=1000)
    fills = (fills_payload.get("data") or []) if fills_payload else []
    in_fills = [f for f in fills if start <= str(f.get("timestamp") or "")[:10] <= end]

    v0, v1 = _nav_return(nav.get("data") or [], start, end)
    total = (v1 / v0 - 1.0) if (v0 and v1) else None
    bench = _bench_return(benchmark, start, end)
    excess = (total - bench) if (total is not None and bench is not None) else None
    costs = sum(float(f.get(k) or 0) for f in in_fills
                for k in ("commission", "stamp_duty", "transfer_fee"))
    turnover = sum(float(f.get("price") or 0) * float(f.get("volume") or 0)
                   for f in in_fills)
    realized = fifo_realized(fills, start, end)
    sectors = _sector_map(list(realized))
    sector_pnl: dict[str, float] = {}
    for code, pnl in realized.items():
        sec = sectors.get(code, "未知")
        sector_pnl[sec] = sector_pnl.get(sec, 0.0) + pnl
    return {
        "deployment_id": deployment_id,
        "period_start": start,
        "period_end": end,
        "total_return": total,
        "excess_return": excess,
        "costs": round(costs, 4),
        "factor": {"realized_pnl": round(sum(realized.values()), 2),
                   "fills": len(in_fills),
                   "buy": sum(1 for f in in_fills
                              if int(f.get("direction") or 0) > 0),
                   "sell": sum(1 for f in in_fills
                               if int(f.get("direction") or 0) < 0),
                   "turnover": round(turnover, 2),
                   "fees": round(costs, 4),
                   "benchmark": benchmark},
        "sector": {k: round(v, 2) for k, v in sector_pnl.items()},
        "stock": {k: round(v, 2) for k, v in realized.items()},
        "regime": _regime(end),
    }


def run_attribution(deployment_id: str, start: str, end: str,
                    benchmark: str = "sh000300", dry_run: bool = False) -> dict:
    from src.research.dao import ResearchDAO

    dao = ResearchDAO()
    dep = dao.get_deployment(deployment_id)
    if not dep:
        raise RuntimeError(f"部署不存在: {deployment_id}")
    data = attribute_period(start, end, benchmark, deployment_id)
    if not dry_run:
        dao.insert_attribution({
            "id": f"A-{start}-{end}-{deployment_id}",
            **data,
            "created_at": now_cn().isoformat(timespec="seconds"),
        })
    return data
