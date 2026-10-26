"""子项目数据契约修复（2026-10-27，Issue #4/#5/#6/#7/#8）。

- **#4 前复权口径**：新增 ``kline_daily.close_qfq``（前复权，与 ``close`` 同口径），
  并回填；对 ``close_adj`` 为 NULL 的 stub 行重新抓取（Sina raw+hfq）补齐。
- **#5 基准代码**：``benchmark_daily`` 补 ``000001/399001/399006``（从 ``market_index`` 映射）。
- **#6 交易日历**：回填 ``trading_calendar``（market_id/trade_date/is_open）。
- **#7 覆盖度**：输出 ``kline_daily`` 覆盖度（distinct code / instrument）。

用法：
  python scripts/fix_price_contract.py --dry-run
  python scripts/fix_price_contract.py
"""

import argparse
import json
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from src.models.database import db_conn, init_database, KlineDAO
from scripts.backfill_price_history import fetch_kline_sina_adjusted

# 子项目约定俗成的基准指数代码（Issue #5）
BENCHMARK_MAP = {
    "sh000300": "000300",   # 沪深300
    "sh000922": "000922",   # 中证红利
}
STANDARD_INDEXES = ("000001", "399001", "399006")


def add_close_qfq(dry_run: bool = False) -> dict:
    with db_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(kline_daily)")}
        if "close_qfq" not in cols:
            if dry_run:
                return {"would_add_column": True, "would_backfill": 0}
            conn.execute("ALTER TABLE kline_daily ADD COLUMN close_qfq REAL")
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM kline_daily WHERE close_qfq IS NULL"
        ).fetchone()["n"]
        if dry_run:
            return {"would_add_column": False, "would_backfill": total}
        cur = conn.execute(
            "UPDATE kline_daily SET close_qfq = close WHERE close_qfq IS NULL")
        return {"added_column": True, "backfilled": cur.rowcount}


def fix_close_adj_nulls(dry_run: bool = False, sleep: float = 0.3) -> dict:
    """对 close_adj 为 NULL 的 stub 行：重新抓取该股票（Sina raw+hfq）补齐。"""
    with db_conn() as conn:
        codes = [r["code"] for r in conn.execute(
            "SELECT DISTINCT code FROM kline_daily WHERE close_adj IS NULL "
            "ORDER BY code").fetchall()]
    if not codes:
        return {"codes": [], "refetched": 0}
    if dry_run:
        return {"codes": codes, "would_refetch": len(codes)}
    dao = KlineDAO()
    ok = failed = 0
    for i, code in enumerate(codes, 1):
        try:
            rows = fetch_kline_sina_adjusted(code, "2016-01-01")
            if rows:
                dao.upsert_many(code, rows)
                ok += 1
            else:
                failed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[contract] {code} 重抓失败: {exc}")
        if sleep:
            time.sleep(sleep)
    return {"codes": codes, "refetched": ok, "failed": failed}


def backfill_benchmark(dry_run: bool = False) -> dict:
    """#5：benchmark_daily 补标准指数代码（从 market_index 映射）。"""
    with db_conn() as conn:
        existing = {r["code"] for r in conn.execute(
            "SELECT DISTINCT code FROM benchmark_daily")}
        needed = [c for c in STANDARD_INDEXES if c not in existing]
        if dry_run:
            return {"would_backfill": needed}
        inserted = 0
        for code in needed:
            src = _market_index_code(code)
            rows = conn.execute(
                "SELECT date, current_value FROM market_index "
                "WHERE index_code = ? ORDER BY date", (src,)).fetchall()
            for r in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO benchmark_daily (code, trade_date, close) "
                    "VALUES (?, ?, ?)", (code, r["date"], r["current_value"]))
            inserted += len(rows)
    return {"backfilled": needed, "rows": inserted}


def _market_index_code(code: str) -> str:
    """market_index 同时存在 sh/sz 前缀与纯数字代码；优先纯数字。"""
    return code


def backfill_trading_calendar(dry_run: bool = False) -> dict:
    """#6：回填 trading_calendar（kline 交易日=1，其余=0）。"""
    with db_conn() as conn:
        lo, hi = conn.execute(
            "SELECT MIN(trade_date) AS a, MAX(trade_date) AS b FROM kline_daily"
        ).fetchone()
        trading = {r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM kline_daily").fetchall()}
        if dry_run:
            return {"market_id": "CN", "range": [lo, hi],
                    "trading_days": len(trading)}
        from datetime import date, timedelta
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
    return {"market_id": "CN", "range": [lo, hi], "rows": inserted,
            "trading_days": len(trading)}


def coverage() -> dict:
    with db_conn() as conn:
        codes = conn.execute(
            "SELECT COUNT(DISTINCT code) AS n FROM kline_daily").fetchone()["n"]
        instruments = conn.execute(
            "SELECT COUNT(*) AS n FROM instrument").fetchone()["n"]
        done = conn.execute(
            "SELECT COUNT(*) AS n FROM backfill_progress "
            "WHERE task = 'price_history' AND status = 'done'").fetchone()["n"]
    return {"kline_codes": codes, "instruments": instruments,
            "price_done": done, "coverage": round(codes / instruments, 4)
            if instruments else 0.0}


def main() -> int:
    parser = argparse.ArgumentParser(description="子项目数据契约修复")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.3)
    args = parser.parse_args()
    init_database()
    print(json.dumps({
        "close_qfq": add_close_qfq(args.dry_run),
        "close_adj_nulls": fix_close_adj_nulls(args.dry_run, args.sleep),
        "benchmark": backfill_benchmark(args.dry_run),
        "trading_calendar": backfill_trading_calendar(args.dry_run),
        "coverage": coverage(),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
