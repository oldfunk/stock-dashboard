"""周度收盘检阅汇总（F2）。默认 dry-run；`--publish` 写 news_weekly。

用法：
    python scripts/news_weekly_review.py --week-end 2026-11-27
    python scripts/news_weekly_review.py --week-end 2026-11-27 --publish
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.news_review.weekly import run_weekly  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="周度收盘检阅汇总")
    ap.add_argument("--week-end", help="周五日期 YYYY-MM-DD（默认本周五）")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    res = run_weekly(args.week_end, dry_run=not args.publish)
    if args.out:
        Path(args.out).write_text(res["content_md"], encoding="utf-8")
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != "content_md"},
                         ensure_ascii=False, indent=2, default=str))
    else:
        print(res["content_md"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
