"""历史估值分位（2026-10-23 P0-5）。

用 ``kline_daily``（前复权）+ ``financial_history``（eps / total_shares /
total_equity / notice_date）+ ``dividend_annual``（cash_per_share）计算
PE / PB / 股息率的 5 / 10 年分位。

- 每个交易日取 ``notice_date <= 当日`` 的最新年报（避免前视）。
- PE = price / EPS；PB = price / BVPS（BVPS = total_equity / total_shares）；
  股息率 = 每股现金分红 / price。
- 分位 = 当前值在历史窗口中的百分位（<= 当前值的比例，越低越便宜）。
- 数据不足（缺价或缺财报）返回 None，不估算。
"""

import logging

logger = logging.getLogger(__name__)


def _prices(code: str) -> list[tuple[str, float]]:
    from src.models.database import db_conn
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT trade_date, close FROM kline_daily WHERE code=? "
            "ORDER BY trade_date ASC", (code,)
        ).fetchall()
    return [(r["trade_date"], r["close"]) for r in rows if r["close"] is not None]


def _reports(code: str) -> list[dict]:
    from src.models.database import db_conn
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT report_date, eps, total_shares, total_equity, notice_date "
            "FROM financial_history WHERE stock_code=? AND report_type='A' "
            "ORDER BY report_date DESC", (code,)
        ).fetchall()
    return [dict(r) for r in rows]


def _dividend(code: str) -> dict | None:
    from src.models.database import db_conn
    with db_conn() as conn:
        row = conn.execute(
            "SELECT cash_per_share FROM dividend_annual WHERE stock_code=? "
            "ORDER BY report_date DESC LIMIT 1", (code,)
        ).fetchone()
    return dict(row) if row else None


def _report_at(reports: list[dict], day: str) -> dict | None:
    best = None
    best_notice = None
    for r in reports:
        nd = r.get("notice_date") or r["report_date"]
        if nd and nd <= day and (best_notice is None or nd > best_notice):
            best, best_notice = r, nd
    return best


def _pct(vals, cur) -> float | None:
    vals = [v for v in vals if v is not None]
    if cur is None or not vals:
        return None
    return round(100.0 * sum(1 for v in vals if v <= cur) / len(vals), 1)


def _shift_years(date_str: str, delta: int) -> str:
    return f"{int(date_str[:4]) + delta}{date_str[4:]}"


def compute_percentiles(code: str, years=(5, 10)) -> dict:
    """返回 {"5": {pe_pct, pb_pct, dy_pct, n}, "10": {...}}；数据不足返回 {}。"""
    prices = _prices(code)
    reports = _reports(code)
    div = _dividend(code)
    if not prices or not reports:
        return {}

    cash = (div or {}).get("cash_per_share")
    series: list[tuple[str, float | None, float | None, float | None]] = []
    for day, close in prices:
        if close is None or close <= 0:
            continue
        r = _report_at(reports, day)
        if r is None:
            continue
        eps = r.get("eps")
        eq, sh = r.get("total_equity"), r.get("total_shares")
        pe = round(close / eps, 2) if eps and eps > 0 else None
        bvps = (eq / sh) if (eq and sh and sh > 0) else None
        pb = round(close / bvps, 2) if bvps and bvps > 0 else None
        dy = round(cash / close * 100, 3) if (cash and cash > 0) else None
        series.append((day, pe, pb, dy))
    if not series:
        return {}

    cur = series[-1]
    out: dict[str, dict] = {}
    today = prices[-1][0]
    for y in years:
        cutoff = _shift_years(today, -y)
        window = [s for s in series if s[0] >= cutoff]
        if len(window) < 30:
            continue
        out[str(y)] = {
            "pe_pct": _pct([w[1] for w in window], cur[1]),
            "pb_pct": _pct([w[2] for w in window], cur[2]),
            "dy_pct": _pct([w[3] for w in window], cur[3]),
            "n": len(window),
        }
    return out
