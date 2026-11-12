"""利息保障倍数回填 CLI（批19-c，2026-11-12）——薄壳，逻辑在 src。

逻辑放 ``src/collector/interest_coverage_backfill.py``：周六维护线程要复用它，
而 ``src`` 不允许反向 import ``scripts``（同 ``trading_calendar`` 先例）。
口径、写入范围、进度表语义、为什么不扩 backfill_financial_extra，全见该模块 docstring。

用法
----
    python scripts/backfill_interest_coverage.py --dry-run
    python scripts/backfill_interest_coverage.py --limit 50 --sleep 0.4
    python scripts/backfill_interest_coverage.py --codes 600519,000001 --force
    python scripts/backfill_interest_coverage.py --status
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from src.collector.interest_coverage_backfill import (  # noqa: E402
    DEFAULT_SLEEP, TASK, run,
)
from src.models.database import FinancialHistoryDAO, init_database  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="interest_coverage 回填（只填 NULL 行，年报 + 季报都填）")
    ap.add_argument("--codes", default=None, help="逗号分隔代码；显式指定即必跑")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                    help=f"每只之间的间隔秒，默认 {DEFAULT_SLEEP}")
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="本次时间预算（秒），0=不限")
    ap.add_argument("--max-consecutive-failures", type=int, default=5,
                    help="连续失败多少只即中止，默认 5")
    ap.add_argument("--force", action="store_true",
                    help="忽略进度表，目标集重新全跑（含上次 no_data/partial）")
    ap.add_argument("--dry-run", action="store_true", help="不写库，只报这次能填几行")
    ap.add_argument("--status", action="store_true", help="只看进度汇总，不跑")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    init_database()
    dao = FinancialHistoryDAO()
    if args.status:
        print(json.dumps(dao.backfill_progress_summary(TASK), ensure_ascii=False))
        return 0
    codes = ([c.strip() for c in args.codes.split(",") if c.strip()]
             if args.codes else None)
    result = run(codes=codes, limit=args.limit, offset=args.offset,
                 sleep=args.sleep, dry_run=args.dry_run,
                 max_seconds=args.max_seconds,
                 max_consecutive_failures=args.max_consecutive_failures,
                 force=args.force)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if (result["status_counts"]["partial"] or result["aborted"]) else 0


if __name__ == "__main__":
    sys.exit(main())