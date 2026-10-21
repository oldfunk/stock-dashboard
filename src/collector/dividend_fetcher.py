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
