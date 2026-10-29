"""分红历史与连续分红年数（2026-10-23 P0-2）。

- 拉取最近 N 年年报分红（补历史）
- 计算截至最新年报的连续分红年数（中断即停）
- 回填 stock_snapshot.dividend_years（无则 NULL）

用法：
  python scripts/backfill_dividend_history.py            # 拉历史 + 回填连续年数
  python scripts/backfill_dividend_history.py --years 10
  python scripts/backfill_dividend_history.py --no-fetch # 只算连续年数，不拉历史
"""

import argparse
import logging
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
# 不切工作目录：src 侧路径均基于 __file__ 生成绝对路径，切了会污染调用方（详见 backfill_financial_extra.py 主入口注释）

from src.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from src.models.database import init_database
from src.collector.dividend_fetcher import (
    refresh_dividend_history, backfill_dividend_years, compute_dividend_years,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="分红历史与连续分红年数")
    parser.add_argument("--years", type=int, default=10, help="拉取年数")
    parser.add_argument("--no-fetch", action="store_true", help="只算连续年数")
    args = parser.parse_args()

    init_database()
    if not args.no_fetch:
        h = refresh_dividend_history(args.years)
        print(f"历史：{h['ok_years']}/{len(h['dates'])} 年成功，{h['records']} 条")
    stats = backfill_dividend_years()
    print(f"连续分红年数：{stats['stocks']} 只")
    years_map = compute_dividend_years()
    dist = {}
    for y in years_map.values():
        dist[y] = dist.get(y, 0) + 1
    top = sorted(dist.items(), key=lambda x: -x[0])[:5]
    print("连续年数分布(前 5):", top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
