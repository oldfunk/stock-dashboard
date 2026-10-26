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


def _weekdays(n, start):
    from datetime import date, timedelta
    day = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _seed_kline(days):
    dao = db_mod.KlineDAO()
    for day in days:
        dao.upsert_many("600519", [{
            "trade_date": day, "open": 1.0, "close": 1.0, "high": 1.0,
            "low": 1.0, "volume": 1.0, "amount": 1e8, "turnover": 0.1,
            "close_adj": 1.0}])


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
    first = {day: get_vector(day).metrics for day in DAYS}
    # 重跑覆盖（幂等），每个交易日的结果一致
    snapshot_range(DAYS[0], DAYS[-1])
    assert {day: get_vector(day).metrics for day in DAYS} == first


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


def test_no_lookahead_uses_snapshot_on_or_before_trade_date(db):
    """前视防护：trade_date 之后快照的数据不得被使用。"""
    from src.research.market_state import get_vector, snapshot_range

    def snap(code, pe, day):
        return {"code": code, "name": "X", "market": "A", "sector": "s", "pe": pe,
                "pb": 1.0, "ps": 1.0, "market_cap": 1.0, "circulating_cap": 1.0,
                "roe": 10.0, "revenue": 1.0, "revenue_growth": 5.0,
                "profit": 1.0, "profit_growth": 5.0, "debt_ratio": 30.0,
                "current_price": 1.0, "high_52w": 1.0, "low_52w": 1.0,
                "is_st": False, "list_date": "2010-01-01", "snapshot_date": day}

    db_mod.StockSnapshotDAO().save_batch([
        snap("600519", 20.0, "2026-10-20"),
        snap("000001", 99.0, "2026-10-26"),  # 晚于交易日 -> 不得用
    ])
    _seed_kline(["2026-10-20"])
    snapshot_range("2026-10-20", "2026-10-20")
    vector = get_vector("2026-10-20")
    assert vector.quality["snapshot_date"] == "2026-10-20"
    assert vector.metrics["pe_median"] == 20.0


def test_dry_run_does_not_write(db):
    from src.research.market_state import get_vector, snapshot_range
    _seed_kline(DAYS)
    result = snapshot_range(DAYS[0], DAYS[-1], dry_run=True)
    assert result["dry_run"] is True
    assert result["dates"] == 5
    assert get_vector(DAYS[0]) is None
    # dry-run 连质量记录也不写
    with db_mod.db_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM data_quality_daily").fetchone()[0] == 0


def test_low_coverage_marks_invalid_and_records_quality(db):
    """R1-5：覆盖率不足 -> invalid + 写质量记录 + 不生成叙事。"""
    from src.research.llm.narrative import should_generate_narrative
    from src.research.market_state import get_vector, snapshot_range
    _seed_kline(["2026-10-26"])
    snapshot_range("2026-10-26", "2026-10-26")
    vector = get_vector("2026-10-26")
    assert vector.status == "invalid"
    assert vector.coverage < 0.8
    assert not should_generate_narrative(vector)
    with db_mod.db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM data_quality_daily "
            "WHERE dataset = 'market_state_daily' AND trade_date = '2026-10-26'"
        ).fetchone()
    assert row is not None and row["status"] == "invalid"


def test_high_coverage_generates_narrative(db):
    from src.research.llm.narrative import should_generate_narrative
    from src.research.market_state import get_vector, snapshot_range

    def snap(code, pe):
        return {"code": code, "name": "X", "market": "A", "sector": "s", "pe": pe,
                "pb": 1.0, "ps": 1.0, "market_cap": 1.0, "circulating_cap": 1.0,
                "roe": 10.0, "revenue": 1.0, "revenue_growth": 5.0,
                "profit": 1.0, "profit_growth": 5.0, "debt_ratio": 30.0,
                "current_price": 1.0, "high_52w": 1.0, "low_52w": 1.0,
                "is_st": False, "list_date": "2010-01-01",
                "snapshot_date": "2026-10-26"}

    db_mod.StockSnapshotDAO().save_batch([snap("600519", 20.0), snap("000001", 10.0)])
    days = _weekdays(65, "2026-08-03")
    _seed_kline(days)
    snapshot_range(days[-1], days[-1])
    vector = get_vector(days[-1])
    assert vector.status == "pass"
    assert vector.coverage >= 0.8
    assert should_generate_narrative(vector)
