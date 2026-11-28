"""收盘检阅巡检（F1 运维化）：缺笔记/章节/引用/建议/面板来源。

用法：
    python scripts/check_news_review.py
    python scripts/check_news_review.py --date 2026-11-27 --require-panel
退出码：0 通过；1 有硬失败；2 无笔记/参数错误。
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


def check(date: str = "", require_panel: bool = False) -> tuple[dict, list[str]]:
    dao = NewsReviewDAO()
    row = dao.get_review(date) if date else dao.latest_review()
    if not row:
        return {}, ["review_missing"]
    metrics = evaluate_review(row)
    failures = list(metrics.get("failures") or [])
    if date and row.get("journal_date") != date:
        failures.append("stale_date")
    if require_panel and metrics.get("portfolio_source") != "paper:panel":
        failures.append("panel_source_missing")
    metrics["failures"] = failures
    metrics["ok"] = not failures
    return metrics, failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="收盘检阅巡检")
    ap.add_argument("--date", help="YYYY-MM-DD（默认最新）")
    ap.add_argument("--require-panel", action="store_true",
                    help="要求组合变量来自 :8081 面板")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    metrics, failures = check(args.date, args.require_panel)
    if args.json:
        print(json.dumps(metrics or {"failures": failures},
                         ensure_ascii=False, indent=2))
    elif not metrics:
        print("没有找到收盘检阅", file=sys.stderr)
    else:
        print(f"# 收盘检阅巡检 {metrics.get('journal_date')}")
        print(f"- 章节 {metrics['sections']}/7；引用 {metrics['citations']}；"
              f"无效 {len(metrics['invalid_citations'])}；建议 "
              f"{len(metrics['advice_hits'])}；面板来源 "
              f"{metrics.get('portfolio_source') or '--'}")
        print("- 通过" if not failures else f"- 失败：{', '.join(failures)}")
    return 0 if not failures else (2 if not metrics else 1)


if __name__ == "__main__":
    raise SystemExit(main())
