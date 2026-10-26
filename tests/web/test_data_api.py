"""只读数据 API 回归测试（供子项目 paper_trading 调阅）。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _exec(db_mod, sql, params=()):
    with db_mod.db_conn() as conn:
        conn.execute(sql, params)


def test_screening_empty(client):
    r = client.get("/api/data/screening")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_screening_returns_candidates(client):
    from src.models import database as db_mod
    _exec(db_mod, "INSERT INTO screening_result (run_id, run_date, code, name, "
                  "score, pe, pb, roe, reason, status) "
                  "VALUES ('r1','2026-10-26','600519','贵州茅台',88.0,20.0,3.0,15.0,'低估','active')")
    r = client.get("/api/data/screening")
    items = r.json()["items"]
    assert items[0]["symbol"] == "600519"
    assert items[0]["score"] == 88.0


def test_universe_search_and_size(client):
    from src.models import database as db_mod
    db_mod.StockSnapshotDAO().save_batch([{
        "code": "600519", "name": "贵州茅台", "market": "A", "sector": "白酒",
        "pe": 20.0, "pb": 3.0, "ps": 1.0, "market_cap": 2e12,
        "circulating_cap": 2e12, "roe": 15.0, "revenue": 1e11,
        "revenue_growth": 10.0, "profit": 5e10, "profit_growth": 12.0,
        "debt_ratio": 20.0, "current_price": 1700.0, "high_52w": 1800.0,
        "low_52w": 1500.0, "is_st": False, "list_date": "2001-08-27",
        "snapshot_date": "2026-10-26"}])
    r = client.get("/api/data/universe/search", params={"query": "600519"})
    assert r.json()["items"][0]["symbol"] == "600519"
    r = client.get("/api/data/universe/search", params={"query": "贵州"})
    assert r.json()["items"][0]["name"] == "贵州茅台"
    assert client.get("/api/data/universe/size").json()["count"] == 1


def test_stock_card(client):
    from src.models import database as db_mod
    db_mod.StockSnapshotDAO().save_batch([{
        "code": "600519", "name": "贵州茅台", "market": "A", "sector": "白酒",
        "pe": 20.0, "pb": 3.0, "ps": 1.0, "market_cap": 2e12,
        "circulating_cap": 2e12, "roe": 15.0, "revenue": 1e11,
        "revenue_growth": 10.0, "profit": 5e10, "profit_growth": 12.0,
        "debt_ratio": 20.0, "current_price": 1700.0, "high_52w": 1800.0,
        "low_52w": 1500.0, "is_st": False, "list_date": "2001-08-27",
        "snapshot_date": "2026-10-26"}])
    _exec(db_mod, "INSERT INTO stock_analysis_history (stock_code, run_id, "
                  "analysis_date, score, ai_analysis, ai_trade_strategy, model, "
                  "created_at) VALUES ('600519','r1','2026-10-26',88.0,'分析','策略','glm','2026-10-26')")
    _exec(db_mod, "INSERT INTO watchlist_notes (code, note, note_type, model, "
                  "created_at) VALUES ('600519','笔记','weekly','glm','2026-10-26')")
    _exec(db_mod, "INSERT INTO watchlist_thesis (code, core_thesis, sell_conditions, "
                  "created_at, updated_at) VALUES ('600519','论点','卖出条件','2026-10-26','2026-10-26')")
    card = client.get("/api/data/stock/600519").json()
    assert card["fundamentals"]["pe"] == 20.0
    assert card["ai_analysis"]["score"] == 88.0
    assert card["notes"][0]["note"] == "笔记"
    assert card["thesis"]["core"] == "论点"


def test_market_state_unavailable(client):
    r = client.get("/api/data/market_state")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"


def test_outcomes(client):
    from src.models import database as db_mod
    _exec(db_mod, "INSERT INTO analysis_outcome (analysis_id, benchmark_code, "
                  "stock_code, analysis_date, entry_date, signal, ret_20, "
                  "excess_20, mos_at_entry, updated_at) "
                  "VALUES (1,'sh000300','600519','2026-07-08','2026-07-09','BUY',0.01,0.005,30.0,'2026-10-01')")
    items = client.get("/api/data/outcomes", params={"code": "600519"}).json()["items"]
    assert items[0]["stock_code"] == "600519"
    assert items[0]["excess_20"] == 0.005


def test_watchlist_and_research(client):
    assert client.get("/api/data/watchlist").json()["symbols"] == []
    assert client.get("/api/data/research/hypotheses").json()["items"] == []
    assert client.get("/api/data/research/experiments").json()["items"] == []
    assert client.get("/api/data/research/decisions").json()["items"] == []
