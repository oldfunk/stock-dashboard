"""回填历史日K（前复权），供历史估值分位 / 回测（2026-10-23 P0-5）。

默认拉取候选池 + 观察池 + 钉选；``--all`` 拉取所有有年报的股票（回测域）。
起点 2016-01-01（覆盖 10 年）。幂等：INSERT OR REPLACE 按 (code, trade_date)。

**断点续跑（2026-10-25）**：处理过的股票写入 ``backfill_progress``
（task=``price_history``），失败/无数据可用 ``--retry-no-data`` 重试。
审计实证：旧脚本只取候选 25 只，导致回测价格域仅 49 只、2016–2019 可选域为 0。

用法：
  python scripts/backfill_price_history.py                 # 候选池+观察池+钉选
  python scripts/backfill_price_history.py --all --limit 100 --sleep 0.3
  python scripts/backfill_price_history.py --codes 600519,000001
  python scripts/backfill_price_history.py --start 2016-01-01
"""

import argparse
import logging
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from src.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from src.models.database import (
    init_database, db_conn, KlineDAO, FinancialHistoryDAO,
    ScreeningResultDAO, WatchlistDAO,
)
from src.models.ai_watchlist import AiWatchlistDAO
from src.collector.akshare_fetcher import fetch_kline_data

PROGRESS_TASK = "price_history"


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


def fundamental_codes() -> list[str]:
    """所有有年报的股票（回测的基本面域）。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM financial_history"
            " WHERE report_type = 'A' ORDER BY stock_code").fetchall()
    return [r["stock_code"] for r in rows]


def pending_codes(codes: list[str], retry_no_data: bool = False) -> list[str]:
    """剔除已处理过的股票（断点续跑）；``no_data`` 默认也跳过。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT code, status FROM backfill_progress WHERE task = ?",
            (PROGRESS_TASK,)).fetchall()
    skip = {r["code"] for r in rows
            if r["status"] == "done" or (r["status"] == "no_data" and not retry_no_data)}
    return [c for c in codes if c not in skip]


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史日K")
    parser.add_argument("--codes", default=None, help="逗号分隔，缺省取候选+观察池+钉选")
    parser.add_argument("--all", action="store_true", help="所有有年报的股票（回测域）")
    parser.add_argument("--start", default="2016-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理股票数（0=不限）")
    parser.add_argument("--sleep", type=float, default=0.3, help="每只之间停顿秒数")
    parser.add_argument("--retry-no-data", action="store_true",
                        help="重试上次无数据的股票")
    args = parser.parse_args()

    init_database()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.all:
        codes = fundamental_codes()
    else:
        codes = target_codes()
    if not codes:
        print("无待回填代码")
        return 0
    codes = pending_codes(codes, args.retry_no_data)
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        print("无待回填代码（均已完成，--retry-no-data 可重试失败项）")
        return 0

    dao = KlineDAO()
    progress = FinancialHistoryDAO()
    ok = 0
    total_rows = 0
    started = time.monotonic()
    for idx, code in enumerate(codes, 1):
        try:
            records = fetch_kline_data(code, start_date=args.start, adjust="qfq")
            if records:
                n = dao.upsert_many(code, records)
                progress.mark_backfill_done(PROGRESS_TASK, code, n, "done")
                ok += 1
                total_rows += n
                logger.info(f"[P0-5] {idx}/{len(codes)} {code}: {n} 条"
                            f"（{records[0]['trade_date']}~{records[-1]['trade_date']}）")
            else:
                progress.mark_backfill_done(PROGRESS_TASK, code, 0, "no_data")
                logger.warning(f"[P0-5] {code}: 无数据")
        except Exception as e:
            progress.mark_backfill_done(PROGRESS_TASK, code, 0, "no_data")
            logger.warning(f"[P0-5] {code} 失败: {e}")
        if args.sleep:
            time.sleep(args.sleep)
    elapsed = round(time.monotonic() - started, 1)
    print(f"回填 {ok}/{len(codes)} 只，共 {total_rows} 条（{elapsed}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
