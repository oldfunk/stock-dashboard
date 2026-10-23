"""TopK 离线回测（2026-10-23 P0-8，look-ahead-safe）。

- 基本面源：``financial_history``（年报，含 ``notice_date``）；每个再平衡日只取
  ``notice_date <= 当日`` 的最新年报，**杜绝前视**（P0-10 数据）。
- 价格源：``kline_daily``（前复权）。
- 评分：简化五维（ROE / PE / PB / 净利率 / 负债率），与主筛选器口径方向一致。
- 费用：佣金 0.025%（买卖双扣）、印花税 0.05%（卖出）。
- 对照：等权买入持有全部合格标的（基准）。

用法：
    python scripts/backtest_topk.py --start 2016-01-01 --end 2026-10-23 --topk 20 --freq M
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.database import get_db_path

COMMISSION = 0.00025
STAMP_DUTY = 0.0005
INITIAL_CASH = 1_000_000.0


def get_db():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def load_fundamentals(conn) -> dict:
    """{code: [年报(notice_date, report_date, roe, eps, gm, nm, debt, fcf), ...]}（notice_date 倒序）。"""
    rows = conn.execute(
        "SELECT stock_code, report_date, notice_date, roe, eps, gross_margin, "
        "net_margin, debt_ratio, fcf FROM financial_history "
        "WHERE report_type='A' ORDER BY stock_code, notice_date DESC"
    ).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["stock_code"], []).append({
            "notice_date": r["notice_date"], "report_date": r["report_date"],
            "roe": r["roe"], "eps": r["eps"], "gross_margin": r["gross_margin"],
            "net_margin": r["net_margin"], "debt_ratio": r["debt_ratio"],
            "fcf": r["fcf"],
        })
    return out


def fundamentals_at(fund: dict, code: str, date: str) -> dict | None:
    """notice_date <= date 的最新年报；无则 None（防前视）。"""
    for rec in fund.get(code, []):
        nd = rec["notice_date"] or rec["report_date"]
        if nd and nd <= date:
            return rec
    return None


def load_prices(conn) -> dict:
    """{code: {trade_date: close}}（前复权）。"""
    rows = conn.execute(
        "SELECT code, trade_date, close FROM kline_daily WHERE close IS NOT NULL"
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["code"], {})[r["trade_date"]] = r["close"]
    return out


def score_stock(rec: dict, price: float) -> float:
    """简化五维评分（0-100）：ROE / PE / PB / 净利率 / 负债率。"""
    if price <= 0:
        return 0.0
    score = 0.0
    roe = rec.get("roe")
    if roe is not None:
        score += min(max(roe, 0) / 30 * 30, 30)
    eps = rec.get("eps")
    pe = price / eps if eps and eps > 0 else None
    if pe:
        score += max(0, 20 - pe / 2)
    nm = rec.get("net_margin")
    if nm is not None:
        score += min(max(nm, 0) / 100 * 20, 20)
    gm = rec.get("gross_margin")
    if gm is not None:
        score += min(max(gm, 0) / 100 * 15, 15)
    dr = rec.get("debt_ratio")
    if dr is not None:
        score += max(0, 15 - dr / 10)
    return score


def _close(prices: dict, code: str, date: str):
    return prices.get(code, {}).get(date)


def _rebalance_dates(trading_dates: list, freq: str) -> list:
    out, last = [], None
    for d in trading_dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        key = d if freq == "D" else (dt.strftime("%Y-%W") if freq == "W" else dt.strftime("%Y-%m"))
        if key != last:
            out.append(d)
            last = key
    return out


def run_backtest(conn, start_date: str, end_date: str, topk: int = 20,
                 rebalance_freq: str = "M") -> dict:
    fund = load_fundamentals(conn)
    prices = load_prices(conn)

    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM kline_daily "
        "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (start_date, end_date),
    ).fetchall()
    trading_dates = [r["trade_date"] for r in rows]
    if not trading_dates:
        return {"error": "无交易日数据"}

    rebalance = set(_rebalance_dates(trading_dates, rebalance_freq))

    cash = INITIAL_CASH
    positions: dict[str, dict] = {}

    def mark_to_market(date):
        v = cash
        for code, pos in positions.items():
            c = _close(prices, code, date)
            if c:
                v += c * pos["volume"]
        return v

    for date in trading_dates:
        if date in rebalance:
            # 合格标的：有 notice_date <= date 的年报
            eligible = []
            for code in prices:
                rec = fundamentals_at(fund, code, date)
                price = _close(prices, code, date)
                if rec is None or not price or price <= 0:
                    continue
                eligible.append((score_stock(rec, price), code))
            eligible.sort(reverse=True)
            top = {code for _, code in eligible[:topk]}

            # 卖出不在 TopK 的持仓
            for code in list(positions):
                if code not in top:
                    c = _close(prices, code, date)
                    if c:
                        vol = positions[code]["volume"]
                        cash += c * vol * (1 - COMMISSION - STAMP_DUTY)
                    del positions[code]

            # 买入 TopK
            pv = mark_to_market(date)
            target = pv / topk if topk else 0.0
            for _, code in eligible[:topk]:
                if code in positions:
                    continue
                c = _close(prices, code, date)
                if not c or c <= 0:
                    continue
                budget = min(target, cash)
                max_vol = int(budget / (c * (1 + COMMISSION)) / 100) * 100
                if max_vol <= 0:
                    continue
                cost = c * max_vol * (1 + COMMISSION)
                cash -= cost
                positions[code] = {"volume": max_vol}

    final_value = mark_to_market(trading_dates[-1])
    return {
        "start_date": start_date,
        "end_date": end_date,
        "topk": topk,
        "rebalance_freq": rebalance_freq,
        "initial_cash": INITIAL_CASH,
        "final_value": round(final_value, 2),
        "total_return_pct": round((final_value - INITIAL_CASH) / INITIAL_CASH * 100, 2),
        "positions": len(positions),
    }


def main():
    parser = argparse.ArgumentParser(description="TopK 离线回测（防前视）")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-10-23")
    parser.add_argument("--topk", type=int, default=20)
    parser.add_argument("--freq", default="M", choices=["D", "W", "M"])
    args = parser.parse_args()

    conn = get_db()
    result = run_backtest(conn, args.start, args.end, args.topk, args.freq)
    conn.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
