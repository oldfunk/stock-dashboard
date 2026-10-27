"""Research OS schema 迁移回归测试（R0-2）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "m.db"))
    db_mod.init_database()
    return db_mod


def _kline(code, day):
    db_mod.KlineDAO().upsert_many(code, [{
        "trade_date": day, "open": 10.0, "close": 11.0, "high": 11.0,
        "low": 9.0, "volume": 100.0, "amount": 1000.0, "turnover": 0.1,
    }])


def _legacy_kline(code, day):
    """模拟迁移前的存量行（instrument_id IS NULL）。

    upsert_many 自 2026-10-27 起写入即带契约列（审计 F1），migrate 回填
    只应命中真正的旧行——用裸 SQL 造旧数据，不走 DAO。
    """
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT INTO kline_daily (code, trade_date, close) VALUES (?, ?, ?)",
            (code, day, 11.0))


def _snap(code, name="测试"):
    db_mod.StockSnapshotDAO().save_batch([{
        "code": code, "name": name, "market": "A", "sector": "测试",
        "pe": 10.0, "pb": 1.0, "ps": 1.0, "market_cap": 100.0,
        "circulating_cap": 90.0, "roe": 10.0, "revenue": 10.0,
        "revenue_growth": 5.0, "profit": 2.0, "profit_growth": 5.0,
        "debt_ratio": 30.0, "current_price": 10.0, "high_52w": 12.0,
        "low_52w": 8.0, "is_st": False, "list_date": "2010-01-01",
        "snapshot_date": "2026-10-26",
    }])


def test_backfill_populates_instrument_id_and_seeds(db):
    from scripts.migrate_research_schema import migrate
    _legacy_kline("600519", "2026-10-01")
    _legacy_kline("000001", "2026-10-01")
    _snap("600519", "贵州茅台")
    _snap("000001", "平安银行")
    result = migrate(dry_run=False)
    assert result["backfilled"] == 2
    assert result["remaining_null"] == 0
    with db_mod.db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT code, market, instrument_id FROM kline_daily "
            "ORDER BY code").fetchall()
    assert [(r["code"], r["market"], r["instrument_id"]) for r in rows] == [
        ("000001", "CN", "CN:000001"),
        ("600519", "CN", "CN:600519"),
    ]
    with db_mod.db_conn() as conn:
        inst = conn.execute(
            "SELECT symbol, exchange, name FROM instrument ORDER BY symbol"
        ).fetchall()
    assert [(r["symbol"], r["exchange"], r["name"]) for r in inst] == [
        ("000001", "SZSE", "平安银行"),
        ("600519", "SSE", "贵州茅台"),
    ]


def test_dry_run_does_not_modify(db):
    from scripts.migrate_research_schema import migrate
    _legacy_kline("600519", "2026-10-01")
    _snap("600519")
    result = migrate(dry_run=True)
    assert result["would_backfill"] == 1
    assert result["would_seed_instruments"] == 1
    with db_mod.db_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM kline_daily "
            "WHERE instrument_id IS NULL").fetchone()["n"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM instrument").fetchone()["n"] == 0


def test_upsert_written_rows_not_recounted_as_legacy(db):
    """契约列进 upsert 后：新写入行不再是 migrate 回填目标（审计 F1 交互）。"""
    from scripts.migrate_research_schema import migrate
    _kline("600519", "2026-10-27")
    result = migrate(dry_run=False)
    assert result["backfilled"] == 0
    assert result["remaining_null"] == 0
