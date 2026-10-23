"""回填历史日K（前复权），供历史估值分位 / 回测（2026-10-23 P0-5）。

默认拉取候选池 + 观察池 + 钉选，起点 2016-01-01（覆盖 10 年）。
幂等：INSERT OR REPLACE 按 (code, trade_date)。

用法：
  python scripts/backfill_price_history.py                 # 候选池+观察池+钉选
  python scripts/backfill_price_history.py --codes 600519,000001
  python scripts/backfill_price_history.py --start 2016-01-01
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

from src.models.database import (
    init_database, KlineDAO, ScreeningResultDAO, WatchlistDAO,
)
from src.models.ai_watchlist import AiWatchlistDAO
from src.collector.akshare_fetcher import fetch_kline_data


def target_codes() -> list[str]:
    codes: set[str] = set()
    try:
        codes.update(c['code'] for c in ScreeningResultDAO().get_latest_results(limit=25))
    except Exception as e:
        logger.warning(f"[P0-5] 候选池读取失败: {e}")
    try:
        codes.update(w['code'] for w in AiWatchlistDAO().get_all())
    except Exception as e:
        logger.warning(f"[P0-5] 观察池读取失败: {e}")
    try:
        codes.update(WatchlistDAO().get_watched_codes())
    except Exception as e:
        logger.warning(f"[P0-5] 钉选读取失败: {e}")
    return sorted(codes)


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史日K")
    parser.add_argument("--codes", default=None, help="逗号分隔，缺省取候选+观察池+钉选")
    parser.add_argument("--start", default="2016-01-01", help="起始日期 YYYY-MM-DD")
    args = parser.parse_args()

    init_database()
    codes = [c.strip() for c in args.codes.split(",")] if args.codes else target_codes()
    if not codes:
        print("无待回填代码")
        return 0

    dao = KlineDAO()
    ok = 0
    total_rows = 0
    for code in codes:
        try:
            records = fetch_kline_data(code, start_date=args.start, adjust="qfq")
            if records:
                n = dao.upsert_many(code, records)
                ok += 1
                total_rows += n
                logger.info(f"[P0-5] {code}: {n} 条（{records[0]['trade_date']}~{records[-1]['trade_date']}）")
            else:
                logger.warning(f"[P0-5] {code}: 无数据")
        except Exception as e:
            logger.warning(f"[P0-5] {code} 失败: {e}")
    print(f"回填 {ok}/{len(codes)} 只，共 {total_rows} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
