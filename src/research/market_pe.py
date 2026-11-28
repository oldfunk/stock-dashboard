"""市场 PE 历史（2026-11-28，R1 估值分位底座）。

口径（已定）：
- 年报 EPS（静态 PE），只用 `notice_date <= 当日` 的已披露年报；
- 价格用不复权 `close_raw`（缺失回退 `close`）；
- 每日取全市场**正 PE 的中位数**，负 EPS/负 PE 不计；
- 5 年窗口；样本覆盖不足由调用方判空。

本模块只读财务/行情，写 `market_pe_history`，不碰筛选/交易。
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Optional

from src.utils import now_cn


def _reports(conn) -> dict[str, list[tuple[str, float]]]:
    rows = conn.execute(
        "SELECT stock_code, notice_date, eps FROM financial_history "
        "WHERE report_type='A' AND eps IS NOT NULL AND eps > 0 "
        "AND notice_date IS NOT NULL AND notice_date != '' "
        "ORDER BY stock_code, notice_date").fetchall()
    out: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        out.setdefault(str(r["stock_code"]).zfill(6), []).append(
            (str(r["notice_date"])[:10], float(r["eps"])))
    return out


def compute_history(conn, start: str, end: str,
                    active_count: int = 5527) -> list[dict]:
    """逐日算全市场正 PE 中位数；返回 [{trade_date, pe_median, sample_size, coverage}]。"""
    reports = _reports(conn)
    by_date: dict[str, list[float]] = {}
    cur_code, cur_reports, ptr = None, [], 0
    rows = conn.execute(
        "SELECT code, trade_date, COALESCE(close_raw, close) AS px "
        "FROM kline_daily WHERE trade_date BETWEEN ? AND ? "
        "AND COALESCE(close_raw, close) > 0 ORDER BY code, trade_date",
        (start, end))
    for r in rows:
        code = str(r["code"]).zfill(6)
        if code != cur_code:
            cur_code, cur_reports, ptr = code, reports.get(code, []), 0
        day, px = str(r["trade_date"])[:10], float(r["px"])
        while ptr < len(cur_reports) and cur_reports[ptr][0] <= day:
            ptr += 1
        if ptr == 0:
            continue
        eps = cur_reports[ptr - 1][1]
        pe = px / eps
        if 0 < pe < 1000:
            by_date.setdefault(day, []).append(pe)
    out = []
    for day in sorted(by_date):
        vals = by_date[day]
        out.append({
            "trade_date": day,
            "pe_median": round(statistics.median(vals), 4),
            "sample_size": len(vals),
            "coverage": round(len(vals) / active_count, 4) if active_count else None,
        })
    return out


def save_history(conn, rows: list[dict]) -> int:
    now = now_cn().isoformat()
    conn.executemany(
        "INSERT INTO market_pe_history (trade_date, pe_median, sample_size, "
        "coverage, created_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(trade_date) DO UPDATE SET pe_median=excluded.pe_median, "
        "sample_size=excluded.sample_size, coverage=excluded.coverage, "
        "created_at=excluded.created_at",
        [(r["trade_date"], r["pe_median"], r["sample_size"],
          r["coverage"], now) for r in rows])
    return len(rows)


def percentile_5y(conn, trade_date: str, min_samples: int = 500) -> Optional[float]:
    """当前 PE 中位数在近 5 年（含当日）的百分位；样本不足返回 None。"""
    cur = conn.execute(
        "SELECT pe_median FROM market_pe_history WHERE trade_date <= ? "
        "ORDER BY trade_date DESC LIMIT 1", (trade_date,)).fetchone()
    if not cur or cur["pe_median"] is None:
        return None
    hist = conn.execute(
        "SELECT pe_median FROM market_pe_history "
        "WHERE trade_date BETWEEN date(?, '-5 years') AND ? "
        "AND pe_median IS NOT NULL", (trade_date, trade_date)).fetchall()
    vals = [float(r["pe_median"]) for r in hist]
    if len(vals) < min_samples:
        return None
    pct = 100.0 * sum(1 for v in vals if v <= float(cur["pe_median"])) / len(vals)
    return round(pct, 1)


def default_range(years: int = 5, end: Optional[str] = None) -> tuple[str, str]:
    end = end or date.today().isoformat()
    start = f"{int(end[:4]) - years}{end[4:]}"
    return start, end
