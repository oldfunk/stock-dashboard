"""真实股息数据采集（2026-10-21）。

数据源：东财 stock_fhps_em(date=年报期末)，一次返回全市场分红送配。
只落“现金分红”口径；股息率 = 每股现金分红 / 现价，分红率 = 每股现金分红 / EPS。
缺数据保持 NULL —— 红利策略据此 fail-closed，不用 0 或行业均值冒充。
"""

import logging
import math
from datetime import datetime

from src.utils import now_cn

logger = logging.getLogger(__name__)


def _num(value):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def latest_annual_report_date(today=None) -> str:
    """最新“应已披露”的年报期末。

    A 股年报最晚 4 月底披露：1-4 月只能确定前一年的前一年；
    5 月起用上一年年报。返回 YYYY-MM-DD。
    """
    today = today or now_cn()
    year = today.year - 1
    if today.month < 5:
        year -= 1
    return f"{year}-12-31"


def normalize_fhps_rows(rows: list[dict], report_date: str) -> list[dict]:
    """把 stock_fhps_em 的原始行规范化。

    - 现金分红比例是“每 10 股派息”，除以 10 得每股。
    - 股息率原始值是小数（0.0213 = 2.13%），统一成百分数。
    - 无现金分红的行不落库（缺数据 ≠ 0 股息）。
    """
    out: dict[str, dict] = {}
    for r in rows or []:
        code = str(r.get("代码") or "").strip().zfill(6)
        if not code or not code.isdigit():
            continue
        cash10 = _num(r.get("现金分红-现金分红比例"))
        if cash10 is None or cash10 <= 0:
            continue
        cash = cash10 / 10.0
        eps = _num(r.get("每股收益"))
        raw_yield = _num(r.get("现金分红-股息率"))
        if raw_yield is not None:
            raw_yield = raw_yield * 100 if abs(raw_yield) < 1 else raw_yield
        rec = {
            "stock_code": code,
            "report_date": report_date,
            "cash_per_share": round(cash, 6),
            "eps": round(eps, 6) if eps is not None else None,
            "reported_yield": round(raw_yield, 4) if raw_yield is not None else None,
            "source": "eastmoney",
        }
        old = out.get(code)
        # 同代码多行时保留现金分红更高的一行（真实年报口径应唯一）
        if old is None or (rec["cash_per_share"] > old["cash_per_share"]):
            out[code] = rec
    return list(out.values())


def refresh_dividends(report_date: str | None = None,
                      config: dict | None = None,
                      save: bool = True) -> dict:
    """拉取并落库最新年报分红。失败抛异常，由调用方决定是否降级。"""
    import akshare as ak
    from src.models.database import DividendDAO

    report_date = report_date or latest_annual_report_date()
    date_arg = report_date.replace("-", "")
    df = ak.stock_fhps_em(date=date_arg)
    rows = df.to_dict("records") if df is not None else []
    records = normalize_fhps_rows(rows, report_date)
    saved = DividendDAO().upsert_many(records) if save else 0
    logger.info(f"[股息] {report_date} 年报：原始 {len(rows)} 行，"
                f"有现金分红 {len(records)} 只，落库 {saved} 条")
    return {"report_date": report_date, "raw": len(rows),
            "records": len(records), "saved": saved}


def ensure_dividend_data(config: dict | None = None) -> dict:
    """数据过期/为空时刷新一次；任何失败只告警，不阻断选股流水线。"""
    from src.models.database import DividendDAO

    config = config or {}
    report_date = latest_annual_report_date()
    refresh_days = int(config.get("dividends", {}).get("refresh_days", 7))
    dao = DividendDAO()
    count = dao.count(report_date)
    last = dao.last_updated()
    fresh = False
    if last:
        try:
            age = now_cn().timestamp() - datetime.fromisoformat(last).timestamp()
            fresh = 0 <= age < refresh_days * 86400
        except (ValueError, TypeError, OSError):
            fresh = False
    if count >= 100 and fresh:
        return {"skipped": True, "report_date": report_date, "records": count}
    try:
        return refresh_dividends(report_date, config)
    except Exception as e:
        logger.warning(f"[股息] 刷新失败（红利策略将 fail-closed）: {e}")
        return {"error": str(e), "report_date": report_date, "records": count}


def _annual_report_dates(years: int = 10) -> list[str]:
    """最近 N 个年报期末（含最新应披露年），按旧->新。"""
    year = int(latest_annual_report_date()[:4])
    return [f"{y}-12-31" for y in range(year - years + 1, year + 1)]


def refresh_dividend_history(years: int = 10, save: bool = True) -> dict:
    """拉取最近 N 年的年报分红（补历史，供"连续分红年数"）。

    单年失败只告警，不阻断其余年份。
    """
    import akshare as ak
    from src.models.database import DividendDAO

    dates = _annual_report_dates(years)
    fetched = ok = 0
    for d in dates:
        try:
            df = ak.stock_fhps_em(date=d.replace("-", ""))
            rows = df.to_dict("records") if df is not None else []
            records = normalize_fhps_rows(rows, d)
            if save:
                DividendDAO().upsert_many(records)
            fetched += len(records)
            ok += 1
        except Exception as e:
            logger.warning(f"[股息] 历史 {d} 拉取失败: {e}")
    logger.info(f"[股息] 历史 {ok}/{len(dates)} 年成功，共 {fetched} 条")
    return {"dates": dates, "ok_years": ok, "records": fetched}


def compute_dividend_years() -> dict:
    """每只股票截至最新年报的连续分红年数（中断即停）。

    从最新年报年倒推：必须有当年的现金分红记录且 >0，且年份连续，才累加；
    任一年缺失/无分红 → 停。返回 {code: years}。
    """
    from src.models.database import db_conn

    with db_conn() as conn:
        rows = conn.execute(
            "SELECT stock_code, report_date, cash_per_share FROM dividend_annual "
            "ORDER BY stock_code, report_date DESC"
        ).fetchall()
    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r["stock_code"], []).append(r)
    latest_year = int(latest_annual_report_date()[:4])
    out: dict[str, int] = {}
    for code, recs in by_code.items():
        years = 0
        expected = latest_year
        for r in recs:  # 已按日期倒序
            y = int(r["report_date"][:4])
            cash = _num(r["cash_per_share"])
            if y != expected or cash is None or cash <= 0:
                break
            years += 1
            expected -= 1
        if years:
            out[code] = years
    return out


def backfill_dividend_years() -> dict:
    """把连续分红年数回填 stock_snapshot（无则置 NULL）。"""
    from src.models.database import db_conn

    years_map = compute_dividend_years()
    with db_conn() as conn:
        conn.execute("UPDATE stock_snapshot SET dividend_years=NULL")
        updated = 0
        for code, y in years_map.items():
            conn.execute(
                "UPDATE stock_snapshot SET dividend_years=? WHERE code=?", (y, code)
            )
            updated += 1
    logger.info(f"[股息] 连续分红年数回填 {updated} 只")
    return {"stocks": len(years_map), "updated": updated}


def backfill_snapshot_dividends(report_date: str | None = None) -> dict:
    """用最新年报分红回填 stock_snapshot 的股息率/分红率/年份（2026-10-23 P0-1）。

    - 股息率 = 每股现金分红 / 现价 × 100；分红率 = 每股现金分红 / EPS × 100
    - 缺价或缺分红保持 NULL（不零填），红利策略据此 fail-closed
    - 幂等：同一数据重复执行结果一致
    """
    from src.models.database import db_conn

    report_date = report_date or latest_annual_report_date()
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT stock_code, report_date, cash_per_share, eps "
            "FROM dividend_annual WHERE report_date = ?",
            (report_date,),
        ).fetchall()
        div = {r["stock_code"]: r for r in rows}
        snaps = conn.execute(
            "SELECT code, current_price FROM stock_snapshot"
        ).fetchall()
        updated = with_yield = with_payout = 0
        for s in snaps:
            code = s["code"]
            price = _num(s["current_price"])
            rec = div.get(code)
            cash = _num(rec["cash_per_share"]) if rec else None
            eps = _num(rec["eps"]) if rec else None
            year = str(rec["report_date"])[:4] if rec else None
            dy = round(cash / price * 100, 3) \
                if (cash is not None and price and price > 0) else None
            pr = round(cash / eps * 100, 2) \
                if (cash is not None and eps and eps > 0) else None
            conn.execute(
                "UPDATE stock_snapshot SET dividend_yield=?, payout_ratio=?, "
                "dividend_year=? WHERE code=?",
                (dy, pr, year, code),
            )
            updated += 1
            if dy is not None:
                with_yield += 1
            if pr is not None:
                with_payout += 1
        logger.info(f"[股息] snapshot 回填 {updated} 行：{with_yield} 有股息率 / "
                    f"{with_payout} 有分红率（分红记录 {len(div)} 只）")
        return {"report_date": report_date, "dividend_stocks": len(div),
                "snapshots": len(snaps), "updated": updated,
                "with_yield": with_yield, "with_payout": with_payout}


def attach_dividends(stocks: list[dict],
                     report_date: str | None = None) -> list[dict]:
    """把年度分红挂到候选股 dict（纯 DB 读，不联网）。

    股息率/分红率算不出来就保持 None；绝不填 0。
    """
    from src.models.database import DividendDAO

    if not stocks:
        return stocks
    report_date = report_date or latest_annual_report_date()
    rows = DividendDAO().latest_by_codes([s.get("code") for s in stocks],
                                         report_date)
    with_yield = 0
    for s in stocks:
        rec = rows.get(s.get("code"))
        if not rec:
            continue
        cash = _num(rec.get("cash_per_share"))
        eps = _num(rec.get("eps"))
        price = _num(s.get("current_price")) or _num(s.get("price"))
        s["dividend_year"] = str(rec.get("report_date") or "")[:4] or None
        s["dividend_cash_per_share"] = cash
        if cash is not None and price and price > 0:
            s["dividend_yield"] = round(cash / price * 100, 3)
            with_yield += 1
        if cash is not None and eps and eps > 0:
            s["payout_ratio"] = round(cash / eps * 100, 2)
    logger.info(f"[股息] 挂载 {len(rows)}/{len(stocks)} 只有分红记录，"
                f"{with_yield} 只有股息率")
    return stocks
