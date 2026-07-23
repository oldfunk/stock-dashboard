"""kline API 路由测试"""
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
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_kline_api_empty(client):
    """无数据返回空 klines"""
    resp = client.get("/api/stock/000792/kline?period=daily")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "000792"
    assert data["period"] == "daily"
    assert data["klines"] == []


def test_kline_api_daily(client):
    """写入日K后 API 返回正确格式"""
    from src.models.database import KlineDAO
    dao = KlineDAO()
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "volume": 12345600, "amount": 2.3e8, "turnover": 1.2},
        {"trade_date": "2025-07-22", "open": 18.5, "close": 19.0,
         "high": 19.2, "low": 18.3, "volume": 9876500, "amount": 1.9e8, "turnover": 0.9},
    ])
    resp = client.get("/api/stock/000792/kline?period=daily")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "000792"
    assert len(data["klines"]) == 2
    k = data["klines"][0]
    assert "timestamp" in k
    assert k["open"] == 18.0
    assert k["close"] == 18.5
    assert k["volume"] == 12345600
    # timestamp 是毫秒级
    assert k["timestamp"] > 1e12


def test_kline_api_weekly_aggregation(client):
    """周K聚合：同一周的日K合并为一条"""
    from src.models.database import KlineDAO
    dao = KlineDAO()
    # 2025-07-21(周一) ~ 2025-07-25(周五) 同一周
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "volume": 10000, "amount": 1e6, "turnover": 1.0},
        {"trade_date": "2025-07-22", "open": 18.5, "close": 19.0,
         "high": 19.2, "low": 18.3, "volume": 20000, "amount": 2e6, "turnover": 2.0},
        {"trade_date": "2025-07-23", "open": 19.0, "close": 18.8,
         "high": 19.5, "low": 18.6, "volume": 15000, "amount": 1.5e6, "turnover": 1.5},
    ])
    resp = client.get("/api/stock/000792/kline?period=weekly")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["klines"]) == 1
    w = data["klines"][0]
    # 周一开盘
    assert w["open"] == 18.0
    # 最后收盘
    assert w["close"] == 18.8
    # 周内最高
    assert w["high"] == 19.5
    # 周内最低
    assert w["low"] == 17.9
    # 成交量求和
    assert w["volume"] == 45000


def test_kline_api_invalid_period(client):
    """无效 period 参数返回 400"""
    resp = client.get("/api/stock/000792/kline?period=invalid")
    assert resp.status_code == 400
