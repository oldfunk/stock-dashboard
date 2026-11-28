"""F2 周度聚合：日期边界、段落抽取、diff、Markdown。"""
from __future__ import annotations

import json

from src.news_review import weekly


def _review(day, md, sources, actions=None):
    return {"journal_date": day, "content_md": md,
            "sources_json": json.dumps(sources),
            "actions_summary": json.dumps(actions or {})}


def test_week_bounds_snaps_weekend_to_friday():
    assert weekly.week_bounds("2026-11-28") == ("2026-11-23", "2026-11-27")
    assert weekly.week_bounds("2026-11-27") == ("2026-11-23", "2026-11-27")


def test_section_extracts_until_next_heading():
    md = "## 1. 市场事实\n事实A\n\n## 2. 持仓\n持仓B\n"
    assert weekly.section(md, 1) == "事实A"
    assert weekly.section(md, 2) == "持仓B"
    assert weekly.section(md, 9) == ""


def test_diff_reviews_repeated_and_new_codes():
    reviews = [
        _review("2026-11-23", "", [{"id": 1, "code": "000792"}]),
        _review("2026-11-24", "", [{"id": 2, "code": "000792"},
                                   {"id": 3, "code": "600519"}]),
    ]
    d = weekly.diff_reviews(reviews)
    assert d["repeated"] == ["000792"]
    assert d["new_on_last"] == ["600519"]
    assert d["dropped_on_last"] == []


def test_build_weekly_contains_sections_sources_and_actual():
    reviews = [
        _review("2026-11-25", "## 1. 市场事实\n沪深300 下跌 [news#1]\n"
                "## 6. 下周关注\n关注 A\n",
                [{"id": 1, "code": "000792", "title": "t1", "url": "u1",
                  "source": "em", "published_at": "2026-11-25"}],
                {"item_count": 3, "holdings_hit": 1,
                 "portfolio_asof": "2026-11-25",
                 "portfolio_actual": {"return_str": "-3.58%", "asof": "2026-11-25"}}),
        _review("2026-11-27", "## 1. 市场事实\n沪深300 上涨 [news#2]\n"
                "## 6. 下周关注\n关注 B\n",
                [{"id": 2, "code": "000792", "title": "t2",
                  "source": "cls", "published_at": "2026-11-27"}],
                {"item_count": 4, "holdings_hit": 0,
                 "portfolio_asof": "2026-11-27",
                 "portfolio_actual": {"return_str": "-2.87%", "asof": "2026-11-27",
                                      "realized_pnl": -2859.54, "fees_total": 40.68,
                                      "mdd_str": "4.17%"}}),
    ]
    out = weekly.build_weekly(reviews, [], "2026-11-23", "2026-11-27")
    md = out["content_md"]
    assert "每日要点" in md and "重复主题" in md and "来源" in md
    assert "000792" in md and "-2.87%" in md and "关注 B" in md
    assert out["actions"]["reviews"] == 2
