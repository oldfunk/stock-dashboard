"""分红税口径与除权除息建议（B4/B5，只建议，不自动改账）。

税口径（A 股个人投资者，简化）：
- 持股 ≤1 个月：20%
- 1 个月 < 持股 ≤1 年：10%
- 持股 >1 年：0%
持股期按**买入日（position.entry_day）→ 除权除息日**计算自然日；
缺 entry_day 时不猜，标 `needs_manual_holding_period`。

除权除息建议：
- 优先读母库 `dividend_annual`（只读契约）；
- 母库不可用时才尝试 akshare `stock_fhps_detail_em`（不稳定的外部接口，
  解析不到就如实 unavailable，不猜除权日/不猜比例）；
- 输出只是建议：真正落账仍走 `corporate apply`，人工确认除权日与比例。
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional
import math


def tax_rate(holding_days: Optional[int]) -> Optional[float]:
    """持股天数 → 税率；未知返回 None（fail-closed，不按 0 处理）。"""
    if holding_days is None:
        return None
    try:
        d = int(holding_days)
    except (TypeError, ValueError):
        return None
    if d < 0:
        return None
    if d <= 30:
        return 0.20
    if d <= 365:
        return 0.10
    return 0.0


def _days_between(entry_day: str, ex_date: str) -> Optional[int]:
    try:
        a = date.fromisoformat(str(entry_day)[:10])
        b = date.fromisoformat(str(ex_date)[:10])
    except (TypeError, ValueError):
        return None
    return (b - a).days


def estimate_tax(positions: Iterable, cash_per_share: float,
                 ex_date: str) -> dict:
    """按持仓估算税前分红/税/税后净额（只读建议）。"""
    dps = float(cash_per_share or 0.0)
    out = {"ex_date": str(ex_date)[:10], "cash_per_share": dps,
           "items": [], "total": {"gross": 0.0, "tax": 0.0, "net": 0.0},
           "basis": "持股期=entry_day→除权日；≤30天20%，31-365天10%，>365天0%",
           "note": "只建议；落账走 corporate apply 人工确认"}
    if dps <= 0:
        out["error"] = "每股红利必须 >0"
        return out
    for p in positions or []:
        sym = str(getattr(p, "symbol", "") or "")
        vol = int(getattr(p, "total_volume", 0) or 0)
        if not sym or vol <= 0:
            continue
        entry = str(getattr(p, "entry_day", "") or "")
        days = _days_between(entry, ex_date) if entry else None
        rate = tax_rate(days)
        gross = round(vol * dps, 2)
        item = {"symbol": sym, "volume": vol, "entry_day": entry or None,
                "holding_days": days, "tax_rate": rate,
                "gross_dividend": gross,
                "tax": (round(gross * rate, 2) if rate is not None else None),
                "net": (round(gross * (1 - rate), 2)
                        if rate is not None else None)}
        if not entry:
            item["needs_manual_holding_period"] = (
                "缺 entry_day，无法判定税率；不要按 0 税入账")
        out["items"].append(item)
        out["total"]["gross"] += gross
        if rate is not None:
            out["total"]["tax"] += gross * rate
            out["total"]["net"] += gross * (1 - rate)
    out["total"] = {k: round(v, 2) for k, v in out["total"].items()}
    out["complete"] = all(i.get("tax") is not None for i in out["items"])
    return out


def _from_mother(symbols: list[str]) -> list[dict]:
    from paper_trading.integration import mother as m

    blocks = m.dividend(symbols) or {}
    events = []
    for sym in symbols:
        blk = blocks.get(sym) or {}
        if not blk.get("available"):
            continue
        rows = blk.get("data") or []
        if not rows:
            continue
        row = rows[0]                       # 最新报告期（SQL 已按日期倒序）
        try:
            dps = float(row.get("cash_per_share") or 0.0)
        except (TypeError, ValueError):
            dps = 0.0
        if dps <= 0:
            continue
        events.append({"symbol": sym, "cash_per_share": dps,
                       "share_ratio": 0.0,
                       "report_date": row.get("report_date"),
                       "ex_date": None,
                       "source": blk.get("source") or "mother:dividend_annual",
                       "asof": blk.get("asof")})
    return events


def _from_akshare(symbols: list[str]) -> list[dict]:
    """akshare 分红送配（可选；任何异常/列不识别都返回空，不猜）。"""
    try:
        import akshare as ak  # type: ignore
    except Exception:                       # noqa: BLE001
        return []
    events = []
    for sym in symbols:
        try:
            df = ak.stock_fhps_detail_em(symbol=sym)
        except Exception:                   # noqa: BLE001 - 外部源不可用
            continue
        try:
            cols = {str(c): c for c in getattr(df, "columns", [])}
            ex_col = next((v for k, v in cols.items() if "除权除息日" in k), None)
            pay_col = next((v for k, v in cols.items()
                            if "现金分红" in k and "比例" in k), None)
            ratio_col = next((v for k, v in cols.items()
                              if "送转" in k and "比例" in k), None)
            if not ex_col or not pay_col or df is None or df.empty:
                continue
            rows = df.dropna(subset=[ex_col]).tail(1).to_dict("records")
        except Exception:                   # noqa: BLE001
            continue
        for row in rows:
            def _num(v):
                try:
                    s = str(v)
                    for token in ("元", "股", "%"):
                        s = s.replace(token, "")
                    x = float(s)
                    return x if math.isfinite(x) else 0.0
                except (TypeError, ValueError):
                    return 0.0
            # 东财“10派X元”口径 → 每股
            dps = _num(row.get(pay_col)) / 10.0
            ratio = _num(row.get(ratio_col)) / 10.0 if ratio_col else 0.0
            if dps <= 0 and ratio <= 0:
                continue
            events.append({"symbol": sym, "cash_per_share": dps,
                           "share_ratio": ratio,
                           "report_date": None,
                           "ex_date": str(row.get(ex_col))[:10],
                           "source": "akshare:stock_fhps_detail_em",
                           "asof": None})
    return events


def suggest(positions: Iterable, *, source: str = "auto",
            use_akshare: bool = False) -> dict:
    """生成“建议调整”清单；不写账本。

    source=auto：母库优先，母库整体不可用时（且 use_akshare=True）才尝试
    akshare。默认不访问外网（use_akshare=False），避免无人值守时挂死。
    """
    pos = [p for p in (positions or [])
           if int(getattr(p, "total_volume", 0) or 0) > 0]
    syms = sorted({str(getattr(p, "symbol", "")) for p in pos if getattr(p, "symbol", "")})
    if not syms:
        return {"ok": True, "events": [], "suggestions": [],
                "note": "无持仓，无需建议"}
    events: list[dict] = []
    src_used = "mother"
    if source in ("auto", "mother"):
        try:
            events = _from_mother(syms)
        except Exception:                   # noqa: BLE001
            events = []
    if not events and source in ("auto", "akshare") and (
            source == "akshare" or use_akshare):
        src_used = "akshare"
        events = _from_akshare(syms)
    out_sugg = []
    for e in events:
        ex = e.get("ex_date")
        item = {**e, "status": "needs_manual_confirm"}
        if not ex:
            item["needs_ex_date"] = ("母库只有报告期，没有除权除息日；"
                                     "请人工确认后补 --ex-date")
        else:
            tax = estimate_tax(pos, e.get("cash_per_share") or 0.0, ex)
            item["tax_estimate"] = tax
            item["command"] = (
                "python -m paper_trading.cli corporate apply "
                f"--symbol {e['symbol']} --ex-date {ex} "
                f"--cash-per-share {e.get('cash_per_share') or 0} "
                f"--share-ratio {e.get('share_ratio') or 0}")
        out_sugg.append(item)
    return {"ok": True, "source": src_used if events else "none",
            "events": events, "suggestions": out_sugg,
            "note": ("只建议，不自动改账；确认除权日/比例后走 corporate apply。"
                     "母库无除权日时不会猜测。")}
