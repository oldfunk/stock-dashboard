"""R4-lite API：设置状态、实验列表、复核（假模型，不落库）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "w.db"))
    db_mod.init_database()
    from src.research.dao import ResearchDAO
    dao = ResearchDAO()
    dao.insert_hypothesis({"id": "H-T1", "statement": "s", "status": "locked"})
    dao.insert_experiment({"id": "E-T1", "hypothesis_id": "H-T1",
                           "status": "done", "verdict": "inconclusive",
                           "plan": {}})
    dao.insert_result({"id": "R-T1", "experiment_id": "E-T1",
                       "metric": "excess", "value": 1.0, "sample_size": 30})
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


class FakeAnalyzer:
    configured = True

    def ask_raw(self, prompt, system=None, timeout=None):
        return ("# 复核\n[H-T1] [E-T1] [R-T1] 证据不足。", "fake", {})


def test_research_status_and_experiments(client):
    assert "enabled" in client.get("/api/llm/research").json()
    data = client.get("/api/research/experiments").json()["data"]
    assert data and data[0]["id"] == "E-T1"


def test_review_endpoint(client, monkeypatch):
    import src.llm_config as llm
    monkeypatch.setattr(llm, "research_analyzer", lambda: FakeAnalyzer())
    body = client.post("/api/research/experiment_review",
                       json={"experiment_id": "E-T1"}).json()
    assert body["ok"] is True
    assert "H-T1" in body["data"]["content_md"]
