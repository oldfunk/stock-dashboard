"""F 线收盘检阅：夹具库 + 假模型，验证发布、来源、事件与降级。"""
from __future__ import annotations

import json

import pytest

from src.models import database as db_mod
from src.utils import now_cn


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(db))
    db_mod.init_database()
    import src.paper_bridge as pb
    monkeypatch.setattr(pb, "load_positions", lambda: {"data": []})
    monkeypatch.setattr(pb, "load_panel_snapshot", lambda **k: {
        "available": False, "source": "paper:panel"})
    monkeypatch.setattr(pb, "load_status", lambda **k: {
        "account": {"data": {"cash": 80000.0}},
        "positions": {"data": [{"symbol": "000792", "total_volume": 400,
                                "available_volume": 400, "avg_cost": 24.0}]},
        "plan": {"data": []},
        "nav": {"data": [{"timestamp": "2026-11-27T15:00:00",
                          "total_value": 97000.0, "pnl_pct": -0.03,
                          "cash": 80000.0, "market_value": 17000.0}]},
        "fills": {"data": []},
    })
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO stock_snapshot (code,name,sector,pe,pb,roe,"
                     "snapshot_date) VALUES ('000792','盐湖股份','化工',14.0,"
                     "3.0,9.0,'2026-11-27')")
        conn.execute("INSERT INTO screening_result (run_id,run_date,code,name,"
                     "score) VALUES ('20261127_153012','2026-11-27','000792',"
                     "'盐湖股份',88.0)")
        conn.executemany("INSERT INTO benchmark_daily VALUES (?,?,?)", [
            ("sh000300", "2026-11-26", 3900.0),
            ("sh000300", "2026-11-27", 4063.0)])
        conn.executemany("INSERT INTO daily_close VALUES (?,?,?,?,?)", [
            ("000792", "2026-11-26", 22.0, 100.0, 10.0),
            ("000792", "2026-11-27", 22.6, 120.0, 12.0),
            ("600519", "2026-11-26", 1400.0, 200.0, 20.0),
            ("600519", "2026-11-27", 1470.0, 180.0, 18.0)])
    return db


class FakeAnalyzer:
    configured = True

    def __init__(self):
        self.calls = []

    def ask_raw(self, prompt, system=None, timeout=None):
        self.calls.append(system)
        if system and "JSON" in system:
            return (json.dumps({
                "market": "指数上涨，价值组合无新增风险",
                "items": [{"news_id": 1, "code": "000792",
                           "relevance": "持仓", "impact": "立案调查需核实",
                           "thesis_link": "未触及", "redline": False}],
                "focus": ["核实公告"]}, ensure_ascii=False),
                "fake-model", {"prompt_tokens": 10})
        return ("# 2026-11-27 收盘检阅（AI 自主）\n\n## 1. 市场事实\n"
                "沪深300 上涨 [news#1]\n\n## 2. 持仓与观察池\n"
                "000792 有立案调查新闻 [news#1]\n\n## 7. 来源\n"
                "- [news#1] 盐湖股份被立案调查\n\n"
                "本笔记由 AI 自主生成，不构成投资建议。",
                "fake-model", {"completion_tokens": 20})


def test_run_close_review_publishes_and_links(env, monkeypatch):
    from src.models.news_review import NewsEventDAO, NewsReviewDAO
    from src.news_review import pipeline

    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO news_item (code,name,title,summary,url,source,"
                     "published_at,fetched_at) VALUES ('000792','盐湖股份',"
                     "'盐湖股份被立案调查','公司公告', '', 'em_global',"
                     "'2026-11-27 10:00:00',?)", (now_cn().isoformat(),))
        news_id = conn.execute("SELECT MAX(id) i FROM news_item").fetchone()["i"]

    fake = FakeAnalyzer()
    res = pipeline.run_close_review(analyzer=fake, date="2026-11-27",
                                    cfg={"news": {"review_max_items": 30}})
    assert res["ok"] is True, res
    assert res["item_count"] == 1
    assert res["event_count"] == 1
    assert len(fake.calls) == 2

    row = NewsReviewDAO().get_review("2026-11-27")
    assert row["note_type"] == "close_review"
    assert row["status"] == "published"
    assert "news#" in row["content_md"]
    assert json.loads(row["sources_json"])[0]["id"] == news_id
    run = NewsReviewDAO().get_run("2026-11-27")
    assert run["status"] == "ok" and run["event_count"] == 1

    ev = NewsEventDAO().recent(trade_date="2026-11-27")
    assert len(ev) == 1 and ev[0]["level"] == "P1"
    with db_mod.db_conn() as conn:
        linked = conn.execute(
            "SELECT entities_json, severity, cluster_id FROM news_item "
            "WHERE id = ?", (news_id,)).fetchone()
    assert "000792" in linked["entities_json"]
    assert linked["severity"] == "P1"
    assert linked["cluster_id"]


def test_run_close_review_no_news_degrades(env):
    from src.models.news_review import NewsReviewDAO
    from src.news_review import pipeline

    res = pipeline.run_close_review(analyzer=FakeAnalyzer(), date="2026-11-28",
                                    cfg={"news": {}})
    assert res["ok"] is True and res["status"] == "degraded"
    row = NewsReviewDAO().get_review("2026-11-28")
    assert row is not None
    assert "今日无可用要闻" in row["content_md"]
    assert NewsReviewDAO().get_run("2026-11-28")["status"] == "degraded"


def test_annotated_review_is_not_overwritten(env):
    from src.models.news_review import NewsReviewDAO
    from src.news_review import pipeline

    pipeline.run_close_review(analyzer=FakeAnalyzer(), date="2026-11-27",
                              cfg={"news": {}})
    dao = NewsReviewDAO()
    assert dao.annotate("2026-11-27", "已核对公告") is True
    before = dao.get_review("2026-11-27")["content_md"]
    pipeline.run_close_review(analyzer=FakeAnalyzer(), date="2026-11-27",
                              cfg={"news": {}})
    after = dao.get_review("2026-11-27")
    assert after["content_md"] == before
    assert after["status"] == "annotated"
    assert json.loads(after["annotations"])[0]["note"] == "已核对公告"
