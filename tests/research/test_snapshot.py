"""研究快照 manifest 回归测试（R0-4）。"""

import json

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "s.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _kline(code, day):
    db_mod.KlineDAO().upsert_many(code, [{
        "trade_date": day, "open": 10.0, "close": 11.0, "high": 11.0,
        "low": 9.0, "volume": 100.0, "amount": 1000.0, "turnover": 0.1,
    }])


def test_create_and_verify_snapshot(db):
    from src.research.snapshot import (
        create_snapshot, verify_snapshot, list_snapshots,
    )
    _kline("600519", "2026-10-01")
    manifest = create_snapshot(["kline_daily"], snapshot_id="DS-TEST-001",
                               random_seed=42, notes="test")
    assert manifest.snapshot_id == "DS-TEST-001"
    assert manifest.random_seed == 42
    assert manifest.tables["kline_daily"].rows == 1
    assert manifest.tables["kline_daily"].max_date == "2026-10-01"
    assert manifest.schema_hash
    path = db.parent.parent / "snapshots" / "DS-TEST-001" / "manifest.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tables"]["kline_daily"]["rows"] == 1
    assert "DS-TEST-001" in list_snapshots()
    assert verify_snapshot("DS-TEST-001").snapshot_id == "DS-TEST-001"


def test_verify_detects_change(db):
    from src.research.snapshot import (
        SnapshotError, create_snapshot, verify_snapshot,
    )
    _kline("600519", "2026-10-01")
    create_snapshot(["kline_daily"], snapshot_id="DS-TEST-002")
    _kline("600519", "2026-10-02")
    with pytest.raises(SnapshotError):
        verify_snapshot("DS-TEST-002")


def test_missing_snapshot_raises(db):
    from src.research.snapshot import SnapshotError, verify_snapshot
    with pytest.raises(SnapshotError):
        verify_snapshot("DS-NOT-EXIST")
