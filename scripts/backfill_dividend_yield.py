"""回填 stock_snapshot 股息率 / 分红率（2026-10-23 P0-1）。

用最新年报现金分红 × 现价重算全市场快照的 dividend_yield / payout_ratio / dividend_year。
缺价或缺分红保持 NULL（不零填），红利策略据此 fail-closed。幂等。

用法：
  python scripts/backfill_dividend_yield.py
  python scripts/backfill_dividend_yield.py --report-date 2025-12-31
  python scripts/backfill_dividend_yield.py --dry-run
"""

import argparse
import logging
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from src.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from src.models.database import init_database
from src.collector.dividend_fetcher import backfill_snapshot_dividends


def main() -> int:
    parser = argparse.ArgumentParser(description="回填快照股息率/分红率")
    parser.add_argument("--report-date", default=None, help="年报期末 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    init_database()
    if args.dry_run:
        from src.models.database import db_conn
        from src.collector.dividend_fetcher import latest_annual_report_date
        report_date = args.report_date or latest_annual_report_date()
        with db_conn() as conn:
            n_div = conn.execute(
                "SELECT COUNT(DISTINCT stock_code) FROM dividend_annual WHERE report_date=?",
                (report_date,),
            ).fetchone()[0]
            n_price = conn.execute(
                "SELECT COUNT(*) FROM stock_snapshot WHERE current_price IS NOT NULL"
            ).fetchone()[0]
        print(f"[dry-run] 年报 {report_date}：分红记录 {n_div} 只 / 有价快照 {n_price} 只")
        return 0

    stats = backfill_snapshot_dividends(args.report_date)
    print(f"年报: {stats['report_date']}")
    print(f"快照: {stats['snapshots']} 行 | 分红记录: {stats['dividend_stocks']} 只")
    print(f"有股息率: {stats['with_yield']} | 有分红率: {stats['with_payout']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
