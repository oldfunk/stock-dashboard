"""每日记录全市场状态（in/out、上市/退市/ST）到 universe_membership。

coverage=current_only：历史成分免费源不完整，不宣称无幸存者偏差。

用法：
  python scripts/research/record_universe.py --dry-run
  python scripts/research/record_universe.py [--date 2026-10-26]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.research.universe import record_daily


def main() -> int:
    parser = argparse.ArgumentParser(description="记录全市场 universe 状态")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    print(json.dumps(record_daily(trade_date=args.date, dry_run=args.dry_run),
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
