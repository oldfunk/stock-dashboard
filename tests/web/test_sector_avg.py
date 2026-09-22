"""行业均值参照（P1② 评分透明化收尾）— DAO 聚合过滤 + 三页展示

只读展示：不改评分逻辑，不进评分链路。
"""

import json

import pytest
from fastapi.testclient import TestClient

SCORE_DETAIL = json.dumps({
    "total": 87.8,
    "roe": {"raw": 15.0, "sub": 80, "weight": 0.3, "contribution": 24.0},
    "pe": {"raw": 12.0, "sub": 70, "weight": 0.25, "contribution": 17.5},
    "growth": {"raw": 10.0, "sub": 60, "weight": 0.2, "contribution": 12.0},
    "debt": {"raw": 30.0, "sub": 75, "weight": 0.15, "contribution": 11.25},
    "margin": {"raw": 40.0, "sub": 78, "weight": 0.1, "contribution": 7.8},
    "consistency_bonus": 5.25,
})


def _snap(code, sector, pe, roe, market="A", name="测试股"):
    return {
        "code": code, "name": name, "market": market, "sector": sector,
        "pe": pe, "pb": 1.0, "ps": 1.0, "market_cap": 100.0, "circulating_cap": 80.0,
        "roe": roe, "revenue": 10.0, "revenue_growth": 1.0, "profit": 2.0,
        "profit_growth": 1.0, "debt_ratio": 20.0, "dividend_yield": 1.0,
        "current_price": 10.0, "high_52w": 12.0, "low_52w": 8.0,
        "is_st": False, "list_date": "2020-01-01", "snapshot_date": "2026-09-22",
    }


@pytest.fixture
def db(tmp_path, monkeypatch):
    """每个测试用独立临时数据库"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    return db_mod


@pytest.fixture
def client(db, monkeypatch):
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _save_screening(code, name):
    from src.models.database import ScreeningResultDAO
    ScreeningResultDAO().save_batch([{
        "run_id": "20260922_001", "run_date": "2026-09-22",
        "code": code, "name": name, "score": 87.8,
        "pe": 12.0, "pb": 2.1, "roe": 18.3,
        "gross_margin": 40.0, "net_margin": 20.0, "ocf_per_share": 1.5,
        "revenue_growth": 15.0, "profit_growth": 20.0, "debt_ratio": 35.0,
        "market_cap": 980.0, "reason": "测试",
        "score_detail": SCORE_DETAIL, "strategy_tags": None,
    }])


def test_sector_averages_filters_and_math(db):
    """聚合只算 A 股非空板块；PE 均值仅取正值样本；roe 全样本"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([
        _snap("600519", "白酒", 30.0, 20.0, name="贵州茅台"),
        _snap("000858", "白酒", 15.0, 15.0, name="五粮液"),
        _snap("601318", "保险", -5.0, 10.0, name="中国平安"),  # 负 PE 不进 PE 均值
        _snap("300001", None, 10.0, 10.0, name="无板块"),
        _snap("HK0001", "白酒", 20.0, 30.0, market="HK", name="港股"),
    ])
    avg = StockSnapshotDAO.get_sector_averages()
    assert set(avg) == {"白酒", "保险"}  # 空板块与非 A 股剔除
    assert avg["白酒"]["avg_pe"] == 22.5   # (30+15)/2，HK 的 20 剔除
    assert avg["白酒"]["avg_roe"] == 17.5
    assert avg["白酒"]["n"] == 2
    assert avg["保险"]["avg_pe"] is None    # 仅剩负 PE → 均值为空
    assert avg["保险"]["avg_roe"] == 10.0


def test_sector_map(db):
    """{code: sector} 同样只含 A 股非空板块"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([
        _snap("600519", "白酒", 30.0, 20.0),
        _snap("300001", None, 10.0, 10.0),
        _snap("HK0001", "白酒", 20.0, 30.0, market="HK"),
    ])
    assert StockSnapshotDAO.get_sector_map() == {"600519": "白酒"}


def test_detail_page_shows_sector_avg(client, db):
    """/stock/{code} 评分拆解块内出现行业均值行（本股板块均值）"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([
        _snap("000792", "化工原料", 12.5, 18.3, name="盐湖股份"),
        _snap("600001", "化工原料", 22.5, 10.0, name="同板块股"),
    ])
    _save_screening("000792", "盐湖股份")

    resp = client.get("/stock/000792")
    assert resp.status_code == 200
    assert "行业均值（化工原料）" in resp.text
    assert "18.3" in resp.text            # roe 均值 (18.3+10)/2 = 14.15
    assert "PE 17.5" in resp.text         # pe 均值 (12.5+22.5)/2
    assert "（2只）" in resp.text


def test_index_shows_sector_avg(client, db):
    """/ 候选卡评分拆解块内出现行业均值行"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([_snap("000792", "化工原料", 12.5, 18.3, name="盐湖股份")])
    _save_screening("000792", "盐湖股份")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "行业均值（化工原料）" in resp.text
    assert "（1只）" in resp.text


def test_watchlist_detail_shows_sector_avg(client, db):
    """/watchlist/{code} 评分拆解块内出现行业均值行"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([_snap("000792", "化工原料", 12.5, 18.3, name="盐湖股份")])
    _save_screening("000792", "盐湖股份")

    resp = client.get("/watchlist/000792")
    assert resp.status_code == 200
    assert "行业均值（化工原料）" in resp.text
    assert "（1只）" in resp.text


def test_no_score_detail_no_sector_row(client, db):
    """没有评分拆解（区块整体隐藏）时不出现行业均值行"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([_snap("000792", "化工原料", 12.5, 18.3, name="盐湖股份")])

    resp = client.get("/stock/000792")
    assert resp.status_code == 200
    assert "行业均值" not in resp.text
