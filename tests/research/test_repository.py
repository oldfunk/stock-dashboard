"""研究仓储接口回归测试（R0-2a）。"""

import pytest

from src.models import database as db_mod

pytest.importorskip("duckdb")

DATES = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
SNAPSHOT = "DS-REPO-1"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "r.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _seed():
    dao = db_mod.KlineDAO()
    for code, trend in (("A", 0.02), ("B", 0.01)):
        rows = []
        for i, day in enumerate(DATES):
            close = 100.0 * (1 + trend) ** i
            rows.append({"trade_date": day, "open": close, "close": close,
                         "high": close, "low": close, "volume": 1.0,
                         "amount": 1.0, "turnover": 0.0, "close_adj": close})
        dao.upsert_many(code, rows)
    from src.research.dao import ResearchDAO
    for day in DATES:
        for code, trend in (("A", 0.02), ("B", 0.01)):
            ResearchDAO().insert_feature_value(f"CN:{code}", day, "F-TREND", 1,
                                               trend, "ok")


def _export():
    from src.research.parquet import export_snapshot
    try:
        return export_snapshot(SNAPSHOT, ["kline_daily", "stock_feature_daily"])
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"duckdb sqlite 扩展不可用: {exc}")


def test_sqlite_repository_query_and_execute(db):
    from src.research.repository import (
        SqliteResearchRepository, get_features, get_prices,
    )
    _seed()
    repo = SqliteResearchRepository()
    assert repo.name == "sqlite"
    assert len(repo.query("SELECT * FROM kline_daily")) == len(DATES) * 2
    assert repo.execute(
        "DELETE FROM stock_feature_daily WHERE value IS NULL") == 0
    prices = get_prices(repo, DATES[0], DATES[-1])
    assert len(prices) == len(DATES) * 2
    assert prices[0]["iid"] == "CN:A"
    features = get_features(repo, "F-TREND", DATES[0])
    assert len(features) == 2


def test_parquet_repository_is_readonly(db):
    _seed()
    _export()
    from src.research.repository import (
        ParquetResearchRepository, RepositoryError, get_prices,
    )
    repo = ParquetResearchRepository(SNAPSHOT)
    assert repo.name == "parquet"
    assert len(get_prices(repo, DATES[0], DATES[-1])) == len(DATES) * 2
    with pytest.raises(RepositoryError):
        repo.execute("DELETE FROM kline_daily")


def test_sqlite_repository_blocks_business_writes(db):
    """回归 G2：研究仓储只写研究表，业务表写入被拒绝。"""
    from src.research.repository import (
        RepositoryError, SqliteResearchRepository,
    )
    _seed()
    repo = SqliteResearchRepository()
    assert repo.execute(
        "INSERT INTO research_hypothesis "
        "(id, prior, statement, universe, status, created_at) "
        "VALUES ('H-9', 'v', 's', 'u', 'draft', '2026-10-26T00:00:00')") == 1
    with pytest.raises(RepositoryError):
        repo.execute("DELETE FROM kline_daily")
    with pytest.raises(RepositoryError):
        repo.execute("UPDATE kline_daily SET close_adj = NULL")
    with pytest.raises(RepositoryError):
        repo.execute("INSERT INTO kline_daily (code, trade_date) "
                     "VALUES ('1', '2026-01-01')")
    with pytest.raises(RepositoryError):
        repo.execute("PRAGMA table_info(kline_daily)")
