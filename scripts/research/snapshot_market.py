"""市场状态快照：对指定交易日范围计算并落库（纯计算、可重跑）。

缺失字段显式 NULL（不补零）；同一日期重跑覆盖（幂等）。

用法：
  python scripts/research/snapshot_market.py --start 2026-10-01 --end 2026-10-26
  python scripts/research/snapshot_market.py --start 2026-10-01 --end 2026-10-26 --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.research.market_state import snapshot_range


def main() -> int:
    parser = argparse.ArgumentParser(description="市场状态快照")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(snapshot_range(args.start, args.end, dry_run=args.dry_run),
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
