"""刷新 AI 分析信号的前向收益（2026-10-21）。

流程：拉基准指数日线 → 按股票聚合分析记录 → 每只股票拉一次前复权 K 线
→ 计算 20/60/120 交易日收益与超额 → 落库 analysis_outcome。
已算出的非空值冻结，不因后续复权变化重算；未到期/缺行情保持 NULL。

用法：
  python scripts/refresh_outcomes.py
  python scripts/refresh_outcomes.py --limit 50
  python scripts/refresh_outcomes.py --dry-run
  python scripts/refresh_outcomes.py --skip-benchmarks
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
from src.models.database import init_database
from src.outcome_service import refresh_benchmarks, refresh_outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description="刷新分析信号前向收益")
    parser.add_argument("--limit", type=int, default=None,
                        help="只处理最近 N 条分析（调试用）")
    parser.add_argument("--dry-run", action="store_true",
                        help="计算但不写 analysis_outcome")
    parser.add_argument("--skip-benchmarks", action="store_true",
                        help="跳过基准指数拉取（沿用库内日线）")
    args = parser.parse_args()

    init_database()
    cfg = load_config()
    if not args.skip_benchmarks:
        stats = refresh_benchmarks(cfg)
        print("基准日线: " + ", ".join(f"{k}={v}" for k, v in stats.items()))

    result = refresh_outcomes(cfg, limit=args.limit, dry_run=args.dry_run)
    print("分析: {analyses} 条 | 股票: {stocks} 只 | 拉K线: {fetched} 只 | "
          "写入: {rows} 行 | 跳过: {skipped} | 已完整: {complete}".format(**result))
    if args.dry_run:
        print("dry-run：未写入数据库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
