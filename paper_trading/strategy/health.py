"""策略健康度（I5a，只读）：滚动超额 / 回撤 / 换手 / 费用拖累。

口径：
- 组合净值取 `nav_history` 同日最后一条；基准取母库 `benchmark_daily`；
- 只在**共同交易日**上计算，窗口取最近 `window` 个共同日；
- 超额 = 组合累计 − 基准累计；`excess_20` 取窗口末尾 20 个共同日；
- 换手 = 窗口内成交金额 / 窗口平均 NAV；费用拖累 = 窗口内三项费用 / 平均 NAV；
- 结论只给建议（healthy/watch/demote），**不直接改交易**；自动降级在 I5b 接入。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

#: 阈值（可按观察结果调整；写在这里而不是散在面板里）
DEMOTE_EXCESS_20 = -0.05     # 20 日超额 ≤ -5pp
DEMOTE_MDD = 0.15            # 窗口回撤 ≥ 15%
DEMOTE_FEE_DRAG = 0.010      # 窗口费用 ≥ 平均 NAV 的 1%
WATCH_EXCESS_20 = 0.0        # 20 日超额 < 0
WATCH_MDD = 0.10
WATCH_FEE_DRAG = 0.005

DEFAULT_WINDOW = 60
DEFAULT_BENCHMARK = "sh000300"


def classify(excess_20: Optional[float], mdd: Optional[float],
             fee_drag: Optional[float]) -> tuple[str, str, list[str]]:
    """返回 (health, recommendation, reasons)。阈值命中顺序：demote → watch → healthy。"""
    reasons: list[str] = []
    if excess_20 is not None and excess_20 <= DEMOTE_EXCESS_20:
        reasons.append(f"20日超额 {excess_20:.2%} ≤ {DEMOTE_EXCESS_20:.0%}")
    if mdd is not None and mdd >= DEMOTE_MDD:
        reasons.append(f"窗口回撤 {mdd:.2%} ≥ {DEMOTE_MDD:.0%}")
    if fee_drag is not None and fee_drag >= DEMOTE_FEE_DRAG:
        reasons.append(f"费用拖累 {fee_drag:.2%} ≥ {DEMOTE_FEE_DRAG:.1%}")
    if reasons:
        return "demote", "observe", reasons
    if excess_20 is not None and excess_20 < WATCH_EXCESS_20:
        reasons.append(f"20日超额 {excess_20:.2%} < 0")
    if mdd is not None and mdd >= WATCH_MDD:
        reasons.append(f"窗口回撤 {mdd:.2%} ≥ {WATCH_MDD:.0%}")
    if fee_drag is not None and fee_drag >= WATCH_FEE_DRAG:
        reasons.append(f"费用拖累 {fee_drag:.2%} ≥ {WATCH_FEE_DRAG:.1%}")
    if reasons:
        return "watch", "watch", reasons
    return "healthy", "active", []


def _ro(path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load(account_db) -> tuple[float, dict[str, float], list[dict]]:
    conn = _ro(account_db)
    try:
        init = 100_000.0
        row = conn.execute("SELECT initial_cash FROM account LIMIT 1").fetchone()
        if row and row[0]:
            init = float(row[0])
        by_day: dict[str, float] = {}
        for r in conn.execute("SELECT timestamp, total_value FROM nav_history ORDER BY id"):
            by_day[str(r["timestamp"])[:10]] = float(r["total_value"])
        fills = [dict(r) for r in conn.execute(
            "SELECT symbol, direction, volume, price, commission, stamp_duty,"
            " transfer_fee, timestamp FROM fills ORDER BY fill_id")]
    finally:
        conn.close()
    return init, by_day, fills


def health_payload(account_db, *, window: int = DEFAULT_WINDOW,
                   benchmark_code: str = DEFAULT_BENCHMARK,
                   today: Optional[str] = None, mother=None,
                   as_of: Optional[str] = None) -> dict:
    from paper_trading.integration import mother as m

    today_s = str(today or date.today().isoformat())[:10]
    init, by_day, fills = _load(account_db)
    if as_of:
        # 回放/报告用：只看到 as_of 当天为止的净值与成交（不含未来数据）
        cut = str(as_of)[:10]
        by_day = {d: v for d, v in by_day.items() if d <= cut}
        fills = [f for f in fills if str(f.get("timestamp"))[:10] <= cut]
    days = sorted(by_day)
    blk = (m.benchmark([benchmark_code], days=max(int(window) * 2, 120),
                       mother=mother) or {}).get(benchmark_code) or {}
    out = {"available": False, "benchmark": benchmark_code,
           "asof": days[-1] if days else None,
           "benchmark_asof": blk.get("asof"), "stale": bool(blk.get("stale")),
           "window": None, "health": None, "recommendation": None,
           "reasons": [], "metrics": {}, "thresholds": {
               "demote_excess_20": DEMOTE_EXCESS_20, "demote_mdd": DEMOTE_MDD,
               "demote_fee_drag": DEMOTE_FEE_DRAG,
               "watch_excess_20": WATCH_EXCESS_20, "watch_mdd": WATCH_MDD,
               "watch_fee_drag": WATCH_FEE_DRAG}}
    if not blk.get("available"):
        out["reason"] = blk.get("note") or "母库 benchmark_daily 不可用"
        return out
    bench = {}
    for r in (blk.get("data") or []):
        try:
            bench[str(r["date"])[:10]] = float(r["close"])
        except (KeyError, TypeError, ValueError):
            continue
    common = sorted(set(by_day) & set(bench))
    if len(common) < 2:
        out["reason"] = f"与基准的共同交易日不足（{len(common)} 天）"
        return out

    wd = common[-max(2, int(window)):]
    start, end = wd[0], wd[-1]
    pr = by_day[end] / by_day[start] - 1
    br = bench[end] / bench[start] - 1
    excess = pr - br
    w20 = wd[-min(20, len(wd)):]
    excess_20 = ((by_day[w20[-1]] / by_day[w20[0]] - 1)
                 - (bench[w20[-1]] / bench[w20[0]] - 1))
    peak, mdd = None, 0.0
    for d in wd:
        v = by_day[d]
        peak = v if peak is None else max(peak, v)
        mdd = max(mdd, 1 - v / peak)

    notional = fees = 0.0
    for f in fills:
        d = str(f.get("timestamp"))[:10]
        if start <= d <= end:
            try:
                amount = float(f["price"]) * float(f["volume"])
            except (KeyError, TypeError, ValueError):
                continue
            notional += amount
            fees += sum(float(f.get(k) or 0.0) for k in
                        ("commission", "stamp_duty", "transfer_fee"))
    avg_nav = sum(by_day[d] for d in wd) / len(wd)
    turnover = notional / avg_nav if avg_nav else 0.0
    fee_drag = fees / avg_nav if avg_nav else 0.0
    health, rec, reasons = classify(excess_20, mdd, fee_drag)
    out.update({
        "available": True,
        "asof": end,
        "stale": bool(blk.get("stale")),
        "window": {"start": start, "end": end, "days": len(wd)},
        "health": health, "recommendation": rec, "reasons": reasons,
        "metrics": {
            "initial_cash": round(init, 4),
            "portfolio_return": round(pr, 6),
            "benchmark_return": round(br, 6),
            "excess": round(excess, 6),
            "excess_20": round(excess_20, 6),
            "max_drawdown": round(mdd, 6),
            "turnover": round(turnover, 8),
            "fee_drag": round(fee_drag, 8),
            "fees_total": round(fees, 4),
            "turnover_notional": round(notional, 4),
            "avg_nav": round(avg_nav, 4),
        },
        "note": "只读建议；自动降级在 I5b 接入",
    })
    return out
