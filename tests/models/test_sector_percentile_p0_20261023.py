"""P0-7 同业估值分位回归测试（2026-10-23）。

- get_sector_averages 返回均值 + 中位数（pe/pb/roe）
- get_sector_percentile 返回个股在行业内的分位（<= value 的比例）
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    return db_mod


def _snap(code, name, sector, pe, roe, pb):
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([{
        "code": code, "name": name, "market": "A", "sector": sector,
        "pe": pe, "pb": pb, "ps": 1.0, "market_cap": 100.0,
        "circulating_cap": 90.0, "roe": roe, "revenue": 10.0,
        "revenue_growth": 5.0, "profit": 2.0, "profit_growth": 5.0,
        "debt_ratio": 40.0, "current_price": 10.0,
        "high_52w": 12.0, "low_52w": 9.0, "is_st": False,
        "list_date": "2000-01-01", "snapshot_date": "2026-10-23",
    }])


def test_sector_averages_has_median(db):
    for i, pe in enumerate([8, 10, 12, 14]):
        _snap(f"00000{i}", f"股{i}", "银行", pe, 10 + i, 0.8 + i * 0.1)
    from src.models.database import StockSnapshotDAO
    avg = StockSnapshotDAO.get_sector_averages()["银行"]
    assert avg["avg_pe"] == 11.0
    assert avg["median_pe"] == 11.0
    assert avg["n"] == 4
    assert avg["avg_roe"] == 11.5


def test_sector_percentile(db):
    for i, pe in enumerate([8, 10, 12, 14]):
        _snap(f"00000{i}", f"股{i}", "银行", pe, 10 + i, 0.8)
    from src.models.database import StockSnapshotDAO
    # pe=8 最低 → 分位 25%（<= 8 的有 1 个 / 4）
    assert StockSnapshotDAO.get_sector_percentile("银行", 8, "pe") == 25.0
    # pe=14 最高 → 100%
    assert StockSnapshotDAO.get_sector_percentile("银行", 14, "pe") == 100.0
    assert StockSnapshotDAO.get_sector_percentile("银行", None, "pe") is None


def test_percentile_ignores_non_positive(db):
    _snap("000001", "亏损股", "地产", -5.0, -1.0, 0.5)
    _snap("000002", "正常股", "地产", 10.0, 8.0, 0.5)
    from src.models.database import StockSnapshotDAO
    # pe=-5 不入样本 → 只有 10；pe=10 → 100%
    assert StockSnapshotDAO.get_sector_percentile("地产", 10, "pe") == 100.0
