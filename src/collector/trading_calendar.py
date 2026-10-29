"""交易日历回填（CN 市场）。

2026-10-29 从 ``scripts/fix_price_contract.py`` 下沉到 src 层（审计 P2-1）：
src 不允许反向 import scripts，而调度器每周要跑这个维护——旧写法
``from scripts.fix_price_contract import backfill_trading_calendar`` 一旦脚本
改名/挪动就只会留下一条 warning，日历静默停更（子项目按 asof 判旧会降级）。
脚本侧保留同名 re-export，旧 import 路径不受影响（同 ``price_model`` 的处理）。

口径：
- 历史（<= kline 最新日）：kline 交易日=1，区间内其余自然日=0（source=kline_daily）。
- 未来（kline 最新日之后）：只按新浪交易日历源（S15）覆盖范围写；
  **源未覆盖的日期不写任何行**（2027 年休市安排发布前不臆造，fail-closed）。
"""

from datetime import date, timedelta

from src.collector.akshare_fetcher import fetch_trade_calendar
from src.models.database import db_conn

# 未来段日历的来源标记（原定义在脚本里，随函数一起下沉，P2-1）
CALENDAR_FUTURE_SOURCE = "sina_trade_date_hist"


def backfill_trading_calendar(dry_run: bool = False) -> dict:
    """#6：回填 trading_calendar（历史 kline 口径 + 未来新浪源口径）。

    - 历史（≤ kline 最新日）：kline 交易日=1，区间内其余自然日=0（source=kline_daily）。
    - 未来（kline 最新日之后）：仅按新浪交易日历源（S15）覆盖范围写入——
      源内交易日=1，源覆盖区间内非交易日=0；**源未覆盖的日期不写任何行**
      （2027 年休市安排未发布前不臆造，fail-closed）。
    """
    with db_conn() as conn:
        lo, hi = conn.execute(
            "SELECT MIN(trade_date) AS a, MAX(trade_date) AS b FROM kline_daily"
        ).fetchone()
        trading = {r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM kline_daily").fetchall()}
        # 未来日历：源拉取失败回 []（fail-closed，不写不猜）
        cal = fetch_trade_calendar()
        if not lo or not hi:  # kline 为空：无历史口径可写（fail-closed）
            future = _future_calendar_plan(hi, trading, cal)
            return {"market_id": "CN", "range": [lo, hi],
                    "trading_days": len(trading), "rows": 0,
                    "future": future}
        if dry_run:
            future = _future_calendar_plan(hi, trading, cal)
            return {"market_id": "CN", "range": [lo, hi],
                    "trading_days": len(trading), "future": future}
        d = date.fromisoformat(lo)
        end = date.fromisoformat(hi)
        inserted = 0
        while d <= end:
            iso = d.isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO trading_calendar "
                "(market_id, trade_date, is_open, source) VALUES (?, ?, ?, ?)",
                ("CN", iso, 1 if iso in trading else 0, "kline_daily"))
            inserted += 1
            d += timedelta(days=1)
        future = _write_future_calendar(conn, hi, trading, cal)
    return {"market_id": "CN", "range": [lo, hi], "rows": inserted,
            "trading_days": len(trading), "future": future}


def _future_calendar_plan(hi: str | None, trading: set,
                          cal: list[str]) -> dict:
    """未来日历计划（不落库；dry-run 与真实写入共用口径）。"""
    if not hi or not cal:
        return {"source": None, "covered_to": None, "trading_days": 0,
                "closed_days": 0}
    src_max = cal[-1]
    if src_max <= hi:
        # 源没有比 kline 更新的日期：无未来段可写
        return {"source": None, "covered_to": None, "trading_days": 0,
                "closed_days": 0}
    d = date.fromisoformat(hi) + timedelta(days=1)
    end = date.fromisoformat(src_max)
    open_days = 0
    closed_days = 0
    while d <= end:
        iso = d.isoformat()
        if iso in cal or iso in trading:
            open_days += 1
        else:
            closed_days += 1
        d += timedelta(days=1)
    return {"source": CALENDAR_FUTURE_SOURCE, "covered_to": src_max,
            "trading_days": open_days, "closed_days": closed_days}


def _write_future_calendar(conn, hi: str | None, trading: set,
                           cal: list[str]) -> dict:
    """把源覆盖范围内的未来日历写入 trading_calendar（源外日期不写）。"""
    plan = _future_calendar_plan(hi, trading, cal)
    if plan["source"] is None:
        return plan
    cal_set = set(cal)
    d = date.fromisoformat(hi) + timedelta(days=1)
    end = date.fromisoformat(plan["covered_to"])
    while d <= end:
        iso = d.isoformat()
        is_open = 1 if (iso in cal_set or iso in trading) else 0
        conn.execute(
            "INSERT OR REPLACE INTO trading_calendar "
            "(market_id, trade_date, is_open, source) VALUES (?, ?, ?, ?)",
            ("CN", iso, is_open, CALENDAR_FUTURE_SOURCE))
        d += timedelta(days=1)
    return plan
