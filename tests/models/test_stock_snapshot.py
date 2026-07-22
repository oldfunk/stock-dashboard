"""StockSnapshotDAO.get_by_code 测试"""

import pytest
from src.models import database as db_mod


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    return db_path


def test_get_by_code_not_found(tmp_db):
    from src.models.database import StockSnapshotDAO
    result = StockSnapshotDAO().get_by_code("999999")
    assert result is None


def test_get_by_code_found(tmp_db):
    from src.models.database import StockSnapshotDAO
    dao = StockSnapshotDAO()
    dao.save_batch([{
        "code": "000792", "name": "盐湖股份", "market": "A", "sector": "化工原料",
        "pe": 12.5, "pb": 2.1, "ps": 3.0, "market_cap": 980.0, "circulating_cap": 900.0,
        "roe": 18.3, "revenue": 100.0, "revenue_growth": 15.0, "profit": 20.0,
        "profit_growth": 20.0, "debt_ratio": 35.0, "dividend_yield": 2.1,
        "current_price": 18.42, "high_52w": 25.0, "low_52w": 12.0,
        "is_st": False, "list_date": "2000-01-01", "snapshot_date": "2026-07-21",
    }])
    result = dao.get_by_code("000792")
    assert result is not None
    assert result["code"] == "000792"
    assert result["name"] == "盐湖股份"
    assert result["sector"] == "化工原料"
    assert result["pe"] == 12.5
