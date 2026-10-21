"""刷新年度现金分红数据（2026-10-21）。

数据源：东财 stock_fhps_em(date=年报期末)，一次拉全市场。
落库表：dividend_annual（每股现金分红 / EPS / 东财披露股息率）。
用法：
  python scripts/refresh_dividends.py
  python scripts/refresh_dividends.py --report-date 2025-12-31
  python scripts/refresh_dividends.py --dry-run
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

from src.config import load_config
from src.models.database import DividendDAO, init_database
from src.collector.dividend_fetcher import (
    latest_annual_report_date, refresh_dividends,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="刷新年度现金分红数据")
    parser.add_argument("--report-date", default=None,
                        help="年报期末 YYYY-MM-DD；默认按披露规则推断")
    parser.add_argument("--dry-run", action="store_true",
                        help="只拉取和规范化，不写库")
    args = parser.parse_args()

    init_database()
    cfg = load_config()
    report_date = args.report_date or latest_annual_report_date()
    try:
        stats = refresh_dividends(report_date, cfg, save=not args.dry_run)
    except Exception as e:
        logger.error(f"[股息] 刷新失败: {e}")
        return 1

    print(f"年报期末: {stats['report_date']}")
    print(f"原始行: {stats['raw']} | 有现金分红: {stats['records']} | 落库: {stats['saved']}")
    if not args.dry_run:
        print(f"库内该期记录: {DividendDAO().count(report_date)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
