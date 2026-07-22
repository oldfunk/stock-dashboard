"""单股详情页 /stock/{code} 路由测试"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个测试用独立临时数据库"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_stock_detail_404_invalid_code_format(client):
    """格式不合法的 code 返回 404"""
    resp = client.get("/stock/abc123")
    assert resp.status_code == 404

    resp = client.get("/stock/12345")  # 5 位
    assert resp.status_code == 404

    resp = client.get("/stock/1234567")  # 7 位
    assert resp.status_code == 404


def test_stock_detail_404_not_in_snapshot(client):
    """合法 code 但 stock_snapshot 里没有也返回 404"""
    resp = client.get("/stock/000792")
    assert resp.status_code == 404


def test_stock_detail_ok_with_snapshot_only(client):
    """只有 stock_snapshot 数据，没有 AI 分析 — 纯数据版"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([{
        "code": "000792", "name": "盐湖股份", "market": "A", "sector": "化工原料",
        "pe": 12.5, "pb": 2.1, "ps": 3.0, "market_cap": 980.0, "circulating_cap": 900.0,
        "roe": 18.3, "revenue": 100.0, "revenue_growth": 15.0, "profit": 20.0,
        "profit_growth": 20.0, "debt_ratio": 35.0, "dividend_yield": 2.1,
        "current_price": 18.42, "high_52w": 25.0, "low_52w": 12.0,
        "is_st": False, "list_date": "2000-01-01", "snapshot_date": "2026-07-21",
    }])

    resp = client.get("/stock/000792")
    assert resp.status_code == 200
    # Header 必有
    assert "盐湖股份" in resp.text
    assert "000792" in resp.text
    assert "化工原料" in resp.text
    # 关键指标快照条
    assert "12.5" in resp.text
    assert "18.3" in resp.text
