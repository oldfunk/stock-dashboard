"""手动跑收盘检阅（F 线，2026-11-28）。

默认 dry-run（不写库）；`--publish` 才写 `ai_journal` 与事件。
用法：
    python scripts/run_news_review.py --date 2026-11-27 --dry-run
    python scripts/run_news_review.py --date 2026-11-27 --publish --out /tmp/review.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.news_review import run_close_review  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="收盘检阅（AI 自主，新闻不进交易）")
    ap.add_argument("--date", help="交易日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--publish", action="store_true", help="写入 ai_journal")
    ap.add_argument("--out", help="正文另存为文件")
    ap.add_argument("--json", action="store_true", help="输出结构化摘要")
    args = ap.parse_args(argv)

    cfg = load_config()
    res = run_close_review(date=args.date, dry_run=not args.publish, cfg=cfg)
    if args.out and res.get("content_md"):
        Path(args.out).write_text(res["content_md"], encoding="utf-8")
    if args.json:
        payload = {k: v for k, v in res.items() if k != "content_md"}
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(res.get("content_md") or "")
        print("\n---")
        print(json.dumps({k: v for k, v in res.items() if k != "content_md"},
                         ensure_ascii=False, default=str))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
