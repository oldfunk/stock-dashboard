"""收盘检阅评估（F5）：自动指标 + 可选人工标注 precision/recall。

用法：
    python scripts/news_review_eval.py --date 2026-11-27
    python scripts/news_review_eval.py --annotations docs/news-review-annotations-template.json
    python scripts/news_review_eval.py --check   # 有硬失败 exit 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.news_review import NewsReviewDAO  # noqa: E402
from src.news_review.evaluate import evaluate_review  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="收盘检阅评估（只读）")
    ap.add_argument("--date", help="YYYY-MM-DD（默认最新一篇）")
    ap.add_argument("--annotations", help="人工标注 JSON")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="硬失败时 exit 1")
    args = ap.parse_args(argv)

    dao = NewsReviewDAO()
    row = dao.get_review(args.date) if args.date else dao.latest_review()
    if not row:
        print("没有找到收盘检阅", file=sys.stderr)
        return 2

    annotations = None
    if args.annotations:
        try:
            annotations = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"标注文件读取失败：{e}", file=sys.stderr)
            return 2

    metrics = evaluate_review(row, annotations)
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    else:
        print(f"# 收盘检阅评估 {metrics['journal_date']}")
        print(f"- 状态：{metrics['status']}；字符 {metrics['chars']}；"
              f"章节 {metrics['sections']}/7")
        print(f"- 来源 {metrics['sources']} 条（带 URL {metrics['sources_with_url']}）"
              f"；引用 {metrics['citations']} 条；无效引用 {len(metrics['invalid_citations'])}")
        print(f"- 买卖建议：{len(metrics['advice_hits'])}；"
              f"组合 asof：{metrics['portfolio_asof']}（{metrics['portfolio_source']}）")
        if "precision" in metrics:
            print(f"- 标注 precision：{metrics['precision']}；"
                  f"recall：{metrics['recall']}（命中 {metrics['annotation_hit']}/"
                  f"{metrics['annotation_relevant']}）")
        if metrics["failures"]:
            print(f"- 硬失败：{', '.join(metrics['failures'])}")
        else:
            print("- 硬检查：通过")
    if args.check and not metrics["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
