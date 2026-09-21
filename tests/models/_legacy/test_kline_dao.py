"""KlineDAO 单元测试"""
import pytest
from src.models.database import KlineDAO, init_database, db_conn, get_db_path


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("src.models.database.get_db_path", lambda: db_path)
    init_database()
    return db_path


def test_upsert_and_get_daily(tmp_db):
    """写入日K并读取，验证字段往返"""
    dao = KlineDAO()
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "volume": 12345600, "amount": 2.3e8, "turnover": 1.2},
        {"trade_date": "2025-07-22", "open": 18.5, "close": 19.0,
         "high": 19.2, "low": 18.3, "volume": 9876500, "amount": 1.9e8, "turnover": 0.9},
    ])
    records = dao.get_daily("000792")
    assert len(records) == 2
    # 升序：7-21 在前
    assert records[0]["trade_date"] == "2025-07-21"
    assert records[0]["close"] == 18.5
    assert records[1]["trade_date"] == "2025-07-22"


def test_upsert_replace(tmp_db):
    """重复写入同一天数据应覆盖"""
    dao = KlineDAO()
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "volume": 12345600, "amount": 2.3e8, "turnover": 1.2},
    ])
    # 覆盖写入
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-21", "open": 18.1, "close": 18.9,
         "high": 19.0, "low": 17.9, "volume": 15000000, "amount": 2.8e8, "turnover": 1.5},
    ])
    records = dao.get_daily("000792")
    assert len(records) == 1
    assert records[0]["close"] == 18.9


def test_get_latest_date(tmp_db):
    """获取最新已缓存日期"""
    dao = KlineDAO()
    assert dao.get_latest_date("000792") is None
    dao.upsert_many("000792", [
        {"trade_date": "2025-07-20", "open": 18.0, "close": 18.0,
         "high": 18.0, "low": 18.0, "volume": 0, "amount": 0, "turnover": 0},
        {"trade_date": "2025-07-22", "open": 19.0, "close": 19.0,
         "high": 19.0, "low": 19.0, "volume": 0, "amount": 0, "turnover": 0},
    ])
    assert dao.get_latest_date("000792") == "2025-07-22"


def test_get_daily_empty(tmp_db):
    """无数据返回空列表"""
    dao = KlineDAO()
    records = dao.get_daily("999999")
    assert records == []


def test_get_daily_limit(tmp_db):
    """limit 参数限制返回条数"""
    dao = KlineDAO()
    dao.upsert_many("000792", [
        {"trade_date": f"2025-07-{i:02d}", "open": 18.0, "close": 18.0,
         "high": 18.0, "low": 18.0, "volume": 0, "amount": 0, "turnover": 0}
        for i in range(1, 11)  # 10 条
    ])
    records = dao.get_daily("000792", limit=5)
    assert len(records) == 5
    # 仍为升序，返回最近 5 条（即 07-06 ~ 07-10）
    assert records[0]["trade_date"] == "2025-07-06"
