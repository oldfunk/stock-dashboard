"""绩效页/API：无纸盘数据也能渲染信号表（夹具库）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "w.db"))
    db_mod.init_database()
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO benchmark_daily VALUES "
                     "('sh000300','2026-11-27',4063.06)")
        conn.execute("INSERT INTO analysis_outcome (analysis_id,benchmark_code,"
                     "stock_code,analysis_date,signal,ret_60,excess_60,updated_at)"
                     " VALUES (1,'sh000300','000792','2026-01-01','BUY',10.0,5.0,'x')")
    import src.performance as perf_mod
    monkeypatch.setattr(perf_mod, "load_nav", lambda limit=250: {
        "source": "unavailable", "data": None, "reason": "test"})
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_performance_page_renders_signal_table(client):
    resp = client.get("/performance")
    assert resp.status_code == 200
    assert "绩效" in resp.text and "AI 信号" in resp.text
    assert "BUY" in resp.text


def test_performance_api(client):
    body = client.get("/api/performance").json()
    assert body["ok"] is True
    assert body["paper"]["available"] is False
    assert body["signals"][0]["signal"] == "BUY"


def test_performance_api_dedupes_analysis_across_benchmarks(client):
    """同一条分析有 sh000300/sh000922 两行；/api/performance 只能算所选基准一次。"""
    from src.models import database as db_mod
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO analysis_outcome (analysis_id,benchmark_code,"
                     "stock_code,analysis_date,signal,ret_60,excess_60,updated_at)"
                     " VALUES (1,'sh000922','000792','2026-01-01','BUY',99.0,88.0,'x')")
    body = client.get("/api/performance").json()
    buy = [x for x in body["signals"] if x["signal"] == "BUY"][0]
    assert buy["n"] == 1
    assert buy["n_60"] == 1
    assert buy["ret_60"] == pytest.approx(10.0)
    assert buy["excess_60"] == pytest.approx(5.0)
