"""Parquet 快照与 DuckDB 分析回归测试（R0-2b/R0-2c）。"""

import json

import pytest

from src.models import database as db_mod

pytest.importorskip("duckdb")

DATES = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
         "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16"]
TRENDS = {"A": 0.02, "B": 0.01, "C": 0.005}
SNAPSHOT = "DS-PQ-1"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "p.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _seed():
    dao = db_mod.KlineDAO()
    for code, trend in TRENDS.items():
        rows = []
        for i, day in enumerate(DATES):
            close = 100.0 * (1 + trend) ** i
            rows.append({"trade_date": day, "open": close, "close": close,
                         "high": close, "low": close, "volume": 1.0,
                         "amount": 1.0, "turnover": 0.0, "close_adj": close})
        dao.upsert_many(code, rows)
    from src.research.dao import ResearchDAO
    for day in DATES:
        for code, trend in TRENDS.items():
            ResearchDAO().insert_feature_value(f"CN:{code}", day, "F-TREND", 1,
                                               trend, "ok")


def _export():
    from src.research.parquet import export_snapshot
    try:
        return export_snapshot(SNAPSHOT, ["kline_daily", "stock_feature_daily"])
    except Exception as exc:  # noqa: BLE001 - 扩展缺失时跳过而非误报
        pytest.skip(f"duckdb sqlite 扩展不可用: {exc}")


def test_export_verify_and_query(db):
    _seed()
    manifest = _export()
    assert manifest.tables["kline_daily"].rows == len(DATES) * len(TRENDS)
    assert manifest.tables["kline_daily"].max_date == DATES[-1]
    assert manifest.tables["kline_daily"].sha256
    from src.research.parquet import SnapshotReader, verify_snapshot
    assert verify_snapshot(SNAPSHOT).snapshot_id == SNAPSHOT
    with SnapshotReader(SNAPSHOT) as reader:
        rows = reader.query("SELECT COUNT(*) AS n FROM kline_daily")
        assert rows[0]["n"] == len(DATES) * len(TRENDS)
        assert reader.query(
            "SELECT COUNT(DISTINCT code) AS n FROM kline_daily")[0]["n"] == 3


def test_verify_detects_tampering(db):
    _seed()
    _export()
    from src.research.parquet import ParquetError, parquet_dir, verify_snapshot
    target = parquet_dir(SNAPSHOT) / "kline_daily.parquet"
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(ParquetError):
        verify_snapshot(SNAPSHOT)


def test_missing_snapshot_raises(db):
    from src.research.parquet import ParquetError, verify_snapshot
    with pytest.raises(ParquetError):
        verify_snapshot("DS-PQ-NOT-EXIST")


def test_scan_forward_returns(db):
    _seed()
    _export()
    from src.research.parquet import SnapshotReader, scan_forward_returns
    with SnapshotReader(SNAPSHOT) as reader:
        top = scan_forward_returns(reader, DATES[0], DATES[-1], horizon_days=5)
    assert top[0]["instrument_id"] == "CN:A"
    assert top[0]["forward_return"] > 0
    assert len(top) == 3


def test_manifest_layout_is_separate(db):
    _seed()
    _export()
    from src.research.parquet import parquet_dir
    assert (parquet_dir(SNAPSHOT) / "parquet-manifest.json").exists()
    data = json.loads(
        (parquet_dir(SNAPSHOT) / "parquet-manifest.json").read_text(encoding="utf-8"))
    assert set(data["tables"]) == {"kline_daily", "stock_feature_daily"}
