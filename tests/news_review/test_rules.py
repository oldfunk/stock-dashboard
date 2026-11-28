"""F 线规则纯函数：实体/级别/聚类/引用/建议检查。"""
from __future__ import annotations

from src.news_review import rules


def test_cluster_key_ignores_punctuation_and_space():
    a = rules.cluster_key("证监会：发布减持新规！")
    b = rules.cluster_key("证监会 发布减持新规")
    assert a == b
    assert len(a) == 12


def test_severity_p0_requires_official_or_strong_keyword():
    official = {"title": "证监会发布减持新规", "summary": "", "code": ""}
    assert rules.severity_for(official, set())[0] == "P0"
    weak = {"title": "某行业关税调整", "summary": "", "code": ""}
    assert rules.severity_for(weak, set())[0] is None
    strong = {"title": "印花税调整落地", "summary": "", "code": ""}
    assert rules.severity_for(strong, set())[0] == "P0"


def test_severity_p1_only_for_target_codes():
    item = {"title": "公司被立案调查", "summary": "", "code": "000792"}
    assert rules.severity_for(item, {"000792"})[0] == "P1"
    assert rules.severity_for(item, {"600519"})[0] is None


def test_entities_match_code_or_name():
    targets = {"000792": "盐湖股份", "600519": "贵州茅台"}
    assert rules.entities_for("盐湖股份发布公告，000792 停牌", targets) == [
        "000792"]
    assert rules.entities_for("茅台批价回升", targets) == []


def test_dedupe_prefers_item_with_url():
    a = {"id": 1, "title": "同一条新闻！", "url": "", "published_at": "x"}
    b = {"id": 2, "title": "同一条新闻", "url": "http://x", "published_at": "y"}
    out = rules.dedupe([a, b])
    assert len(out) == 1 and out[0]["id"] == 2


def test_rank_puts_holdings_before_market():
    items = [{"code": "", "title": "市场", "published_at": "2026-11-27 12:00"},
             {"code": "000792", "title": "持仓", "published_at": "2026-11-27 09:00"}]
    out = rules.rank(items, {"000792": "持仓"})
    assert out[0]["code"] == "000792"


def test_citation_and_advice_checks():
    md = "事实 [news#1]，另一条 [news#9]。建议买入 000792。"
    assert rules.referenced_ids(md) == {1, 9}
    assert rules.missing_citations(md, [1, 2]) == [9]
    assert rules.advice_hits(md)


def test_sanitize_strips_citations_only_from_db_fact_sections():
    md = ("## 1. 市场事实\n沪深300 上涨 [news#1]\n"
          "## 2. 持仓与观察池\n000792 有公告 [news#2]\n"
          "## 4. 组合变量\nNAV 97125 [news#3]\n"
          "## 5. 纪律与风险\n无 [news#4]")
    out = rules.sanitize_db_fact_citations(md)
    assert "[news#1]" not in out and "[news#3]" not in out
    assert "[news#2]" in out and "[news#4]" in out


def test_build_events_caps_and_keys():
    targets = {"000792": "盐湖股份"}
    items = [{"id": 1, "title": "盐湖股份被立案调查", "code": "000792",
              "published_at": "2026-11-27 10:01", "source": "em_global"},
             {"id": 2, "title": "盐湖股份被立案调查", "code": "000792",
              "published_at": "2026-11-27 10:12", "source": "cls_telegraph"}]
    events = rules.build_events(items, targets, now="2026-11-27 15:45:00")
    assert len(events) == 1
    assert events[0]["level"] == "P1"
    assert events[0]["event_key"].startswith("P1:000792:")
