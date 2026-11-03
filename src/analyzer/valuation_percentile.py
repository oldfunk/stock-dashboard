"""历史估值分位（2026-10-23 P0-5，2026-10-25 修前视与股本口径）。

用 ``kline_daily``（不复权 ``close_raw``，估值口径）+ ``financial_history``（eps / net_profit /
total_shares / total_equity / notice_date）+ ``dividend_annual``（cash_per_share）
计算 PE / PB / 股息率的 5 / 10 年分位。

- 每个交易日只取 ``notice_date <= 当日`` 的最新年报；**没有 notice_date 的年报
  不参与**（不回退 ``report_date``——会计年末与实际披露差约 3 个月，回退即前视）。
- PE = price / EPS；PB = price / BVPS（BVPS = total_equity / 当年股本）。
- **当年股本**优先用 ``net_profit / eps`` 反推（``financial_history.total_shares``
  是当前股本盖到所有年份；审计实证 815/958 只年际不变）；无法反推的历史年份
  PB 记 NULL，不拿当前股本硬算。最新年报允许用 total_shares（此时它就是当年值）。
- 股息率 = 当日**已披露**的最近一期每股现金分红 / 不复权价（U7，2026-11-03：
  旧实现把"今天库里的最新分红"套满全历史，那条序列只是价格分位的倒数）。
- 分位 = 当前值在历史窗口中的百分位（<= 当前值的比例，越低越便宜）。
- 数据不足（缺价或缺财报）返回 None，不估算。
"""

import logging

logger = logging.getLogger(__name__)


def _prices(code: str) -> list[tuple[str, float]]:
    """估值分位用**不复权**价（与当期 EPS/BVPS 同口径；旧数据回退 close）。"""
    from src.models.database import db_conn
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT trade_date, COALESCE(close_raw, close) AS px "
            "FROM kline_daily WHERE code=? "
            "ORDER BY trade_date ASC", (code,)
        ).fetchall()
    return [(r["trade_date"], r["px"]) for r in rows if r["px"] is not None]


def _reports(code: str) -> list[dict]:
    from src.models.database import db_conn
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT report_date, eps, net_profit, total_shares, total_equity, "
            "notice_date FROM financial_history "
            "WHERE stock_code=? AND report_type='A' "
            "ORDER BY report_date DESC", (code,)
        ).fetchall()
    return [dict(r) for r in rows]


def _dividends(code: str) -> list[dict]:
    """每股现金分红时间线（附披露时点），供 PIT 取数。

    `dividend_annual` 自己没有披露日，披露时点取同 `(stock_code, report_date)`
    年报的 `notice_date`——与本模块对年报的取数口径完全一致（PE/PB 也这么取）。
    """
    from src.models.database import db_conn
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT d.report_date AS report_date, "
            "       d.cash_per_share AS cash, f.notice_date AS notice_date "
            "FROM dividend_annual d "
            "LEFT JOIN financial_history f "
            "       ON f.stock_code = d.stock_code "
            "      AND f.report_date = d.report_date "
            "WHERE d.stock_code = ? ORDER BY d.report_date DESC", (code,)
        ).fetchall()
    return [dict(r) for r in rows]


def _cash_at(dividends: list[dict], day: str) -> float | None:
    """截止某日**已披露**的最近一期每股现金分红（与 `_report_at` 同一条 PIT 规则）。

    审计 **U7**（2026-11-03 复核成立）：旧实现只取库里最新一条分红，然后把这个
    常数套到全部历史交易日——于是 `dy = 常数 / price`，它的分位其实是**价格分位的
    倒数**，回答不了"股息率现在处于历史什么位置"。分红会变（增派/减派/停派），
    所以必须按时间线取当日已知的那一期。缺披露日的分红行不参与（不回退
    report_date，与年报同一条不前视的规则）。
    """
    best_cash = None
    best_notice = None
    for d in dividends:
        nd = d.get("notice_date")
        if nd and nd <= day and (best_notice is None or nd > best_notice):
            best_cash, best_notice = d.get("cash"), nd
    return best_cash


def _report_at(reports: list[dict], day: str) -> dict | None:
    """取 ``notice_date <= day`` 的最新年报。

    没有 ``notice_date`` 的年报不参与（**不回退 report_date**，否则前视）。
    """
    best = None
    best_notice = None
    for r in reports:
        nd = r.get("notice_date")
        if nd and nd <= day and (best_notice is None or nd > best_notice):
            best, best_notice = r, nd
    return best


def _shares_at(r: dict, latest_report_date: str) -> float | None:
    """当年总股本：优先 ``net_profit / eps`` 反推。

    ``total_shares`` 在库里是当前股本盖到所有年份（审计实证）。
    最新年报可用它（此时就是当年值）；历史年份无法反推则返回 None（不硬算）。
    """
    net_profit, eps = r.get("net_profit"), r.get("eps")
    if net_profit and eps and eps > 0 and net_profit > 0:
        return net_profit / eps
    if r.get("report_date") == latest_report_date:
        return r.get("total_shares")
    return None


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
    dividends = _dividends(code)
    if not prices or not reports:
        return {}

    latest_rd = reports[0]["report_date"] if reports else ""
    series: list[tuple[str, float | None, float | None, float | None]] = []
    for day, close in prices:
        if close is None or close <= 0:
            continue
        r = _report_at(reports, day)
        if r is None:
            continue
        eps = r.get("eps")
        eq = r.get("total_equity")
        sh = _shares_at(r, latest_rd)
        pe = round(close / eps, 2) if eps and eps > 0 else None
        bvps = (eq / sh) if (eq and sh and sh > 0) else None
        pb = round(close / bvps, 2) if bvps and bvps > 0 else None
        # U7：当日的分红 = 那天**已经披露**的最近一期，不是今天才知道的最新一期
        cash = _cash_at(dividends, day)
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
