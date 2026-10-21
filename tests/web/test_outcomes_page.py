"""信号验证页面/API 单测（2026-10-21）。hermetic：tmp 库。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
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


def _outcome(**kw):
    base = {
        "analysis_id": 1, "benchmark_code": "sh000300",
        "stock_code": "600519", "stock_name": "贵州茅台",
        "analysis_date": "2026-09-01", "signal": "BUY",
        "confidence": "高", "verdict": "通过",
        "entry_date": "2026-09-02", "entry_price": 10.0,
        "ret_20": 10.0, "ret_60": None, "ret_120": None,
        "bench_20": 2.0, "bench_60": None, "bench_120": None,
        "excess_20": 8.0, "excess_60": None, "excess_120": None,
    }
    base.update(kw)
    return base


def test_empty_page(client):
    r = client.get("/outcomes")
    assert r.status_code == 200
    assert "信号验证" in r.text
    assert "还没有已到期数据" in r.text


def test_page_with_data(client):
    from src.models.database import AnalysisOutcomeDAO
    AnalysisOutcomeDAO().upsert_many([_outcome()])
    r = client.get("/outcomes?benchmark=sh000300&horizon=20")
    assert r.status_code == 200
    assert "贵州茅台" in r.text
    assert "+8.00%" in r.text

    r2 = client.get("/outcomes?benchmark=sh000922")
    assert r2.status_code == 200
    assert "还没有已到期数据" in r2.text


def test_api(client):
    from src.models.database import AnalysisOutcomeDAO
    AnalysisOutcomeDAO().upsert_many([_outcome()])
    r = client.get("/api/outcomes?benchmark=sh000300")
    assert r.status_code == 200
    data = r.json()
    assert data["benchmark"] == "sh000300"
    assert data["rows"] == 1
    g = data["summary"]["20"][0]
    assert g["signal"] == "BUY"
    assert g["n"] == 1
    assert g["avg_excess"] == 8.0
