"""收盘检阅/事件 API：空态、写入后读取、批注 404（夹具库）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "w.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_review_and_events_empty_state(client):
    assert client.get("/api/news/review/latest").json()["data"] is None
    assert client.get("/api/news/review/list").json()["data"] == []
    assert client.get("/api/news/events").json()["data"] == []
    assert client.post("/api/news/review/2026-11-27/annotate",
                       json={"note": "x"}).status_code == 404


def test_review_api_parses_json_fields(client):
    from src.models.news_review import NewsReviewDAO
    dao = NewsReviewDAO()
    dao.save_review(
        "2026-11-27", "2026-11-27 收盘检阅（AI 自主）", "# 正文 [news#1]",
        [{"id": 1, "title": "t", "url": "u"}],
        {"market": {"asof": "2026-11-27"}},
        {"item_count": 1, "holdings_hit": 1}, run_id="news_review:2026-11-27",
        model="fake")
    body = client.get("/api/news/review/2026-11-27").json()
    assert body["ok"] is True
    assert body["data"]["note_type"] == "close_review"
    assert body["data"]["sources_json"][0]["id"] == 1
    assert body["data"]["actions_summary"]["holdings_hit"] == 1
    latest = client.get("/api/news/review/latest").json()["data"]
    assert latest["journal_date"] == "2026-11-27"

    assert client.post("/api/news/review/2026-11-27/annotate",
                       json={"note": "核对"}).status_code == 200
    row = dao.get_review("2026-11-27")
    assert row["status"] == "annotated"


def test_news_page_renders(client):
    resp = client.get("/news")
    assert resp.status_code == 200
    assert "新闻检阅" in resp.text
