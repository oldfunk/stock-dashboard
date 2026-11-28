"""F5 评估：章节/引用/建议硬检查 + 标注 precision/recall。"""
from __future__ import annotations

import json

from src.news_review.evaluate import REQUIRED_SECTIONS, evaluate_review


def _row(md: str) -> dict:
    return {
        "journal_date": "2026-11-27",
        "status": "published",
        "content_md": md,
        "sources_json": json.dumps([{"id": 1, "url": "u"},
                                    {"id": 2, "url": ""}]),
        "actions_summary": json.dumps({"portfolio_asof": "2026-11-27",
                                       "portfolio_source": "paper:panel",
                                       "item_count": 2}),
    }


def test_evaluate_ok_with_full_sections_and_valid_citations():
    md = "# 收盘检阅\n\n" + "\n".join(REQUIRED_SECTIONS) + "\n事实 [news#1]\n"
    m = evaluate_review(_row(md))
    assert m["ok"] is True
    assert m["sections"] == 7
    assert m["citations"] == 1 and m["invalid_citations"] == []
    assert m["portfolio_source"] == "paper:panel"


def test_evaluate_flags_missing_section_invalid_citation_and_advice():
    md = "## 1. 市场事实\n建议买入 000792 [news#9]\n"
    m = evaluate_review(_row(md))
    assert m["ok"] is False
    assert "missing_sections" in m["failures"]
    assert m["invalid_citations"] == [9]
    assert "advice_present" in m["failures"]


def test_evaluate_annotations_precision_recall():
    md = "# 收盘检阅\n\n" + "\n".join(REQUIRED_SECTIONS) + "\n[news#1] [news#2]\n"
    ann = {"items": {"1": {"relevant": True}, "2": {"relevant": False}}}
    m = evaluate_review(_row(md), ann)
    assert m["annotation_hit"] == 1
    assert m["precision"] == 0.5
    assert m["recall"] == 1.0
