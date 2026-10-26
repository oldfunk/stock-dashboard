"""市场状态快照回归测试（R1-2）。"""

import pytest

from src.models import database as db_mod

DAYS = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "snap.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _seed_kline(days):
    dao = db_mod.KlineDAO()
    for day in days:
        dao.upsert_many("600519", [{
            "trade_date": day, "open": 1.0, "close": 1.0, "high": 1.0,
            "low": 1.0, "volume": 1.0, "amount": 1e8, "turnover": 0.1}])


def test_snapshot_range_writes_five_days(db):
    from src.research.market_state import snapshot_range
    _seed_kline(DAYS)
    result = snapshot_range(DAYS[0], DAYS[-1])
    assert result["dates"] == 5
    assert result["written"] == 5


def test_snapshot_is_reproducible(db):
    from src.research.market_state import get_vector, snapshot_range
    _seed_kline(DAYS)
    snapshot_range(DAYS[0], DAYS[-1])
    first = get_vector(DAYS[0]).metrics
    # 重跑覆盖（幂等），结果一致
    snapshot_range(DAYS[0], DAYS[-1])
    assert get_vector(DAYS[0]).metrics == first
    assert get_vector(DAYS[-1]).metrics == first


def test_missing_fields_are_explicit_null(db):
    from src.research.market_state import get_vector, snapshot_range
    _seed_kline(["2026-10-26"])
    snapshot_range("2026-10-26", "2026-10-26")
    vector = get_vector("2026-10-26")
    assert vector.metrics["total_turnover_yi"] == 1.0
    assert "pe_median" in vector.missing
    assert vector.quality["snapshot_date"] is None


def test_get_and_list_vectors(db):
    from src.research.market_state import get_vector, list_vectors, snapshot_range
    _seed_kline(DAYS)
    snapshot_range(DAYS[0], DAYS[-1])
    assert get_vector("2026-10-20") is not None
    assert get_vector("1999-01-01") is None
    rows = list_vectors()
    assert len(rows) == 5
    assert rows[0]["trade_date"] == DAYS[-1]


def test_dry_run_does_not_write(db):
    from src.research.market_state import get_vector, snapshot_range
    _seed_kline(DAYS)
    result = snapshot_range(DAYS[0], DAYS[-1], dry_run=True)
    assert result["dry_run"] is True
    assert result["dates"] == 5
    assert get_vector(DAYS[0]) is None
