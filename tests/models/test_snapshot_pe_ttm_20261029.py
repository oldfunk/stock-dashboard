"""stock_snapshot 落 pe_ttm 列（2026-10-29，审计 N8①）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    return db_mod


def test_snapshot_persists_pe_ttm(db):
    db.StockSnapshotDAO().save_batch([{
        "code": "600519", "name": "贵州茅台", "snapshot_date": "2026-10-29",
        "pe": 20.79, "pe_ttm": 19.01, "pb": 6.74, "current_price": 1348.61,
    }])
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT pe, pe_ttm FROM stock_snapshot WHERE code='600519'").fetchone()
    assert row["pe"] == 20.79
    assert row["pe_ttm"] == 19.01


def test_snapshot_pe_ttm_missing_writes_null(db):
    """无 TTM 来源（自算兜底）：列写 NULL 不冒充，门回退 pe。"""
    db.StockSnapshotDAO().save_batch([{
        "code": "000001", "name": "平安银行", "snapshot_date": "2026-10-29",
        "pe": 5.0, "pb": 0.6,
    }])
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT pe, pe_ttm FROM stock_snapshot WHERE code='000001'").fetchone()
    assert row["pe"] == 5.0
    assert row["pe_ttm"] is None
