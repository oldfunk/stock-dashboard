"""价格契约修复回归测试（Issue #4/#5/#6/#7/#8）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "c.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def test_add_close_qfq(db):
    from scripts.fix_price_contract import add_close_qfq
    db_mod.KlineDAO().upsert_many("600519", [{
        "trade_date": "2026-10-26", "open": 1.0, "close": 1700.0,
        "high": 1.0, "low": 1.0, "volume": 1.0, "amount": 1.0,
        "turnover": 0.1, "close_raw": 1700.0, "close_adj": 1700.0}])
    result = add_close_qfq(dry_run=False)
    assert result["backfilled"] == 1
    with db_mod.db_conn() as conn:
        assert conn.execute(
            "SELECT close_qfq FROM kline_daily WHERE code='600519'"
        ).fetchone()[0] == 1700.0


def test_backfill_benchmark_maps_standard_codes(db):
    from scripts.fix_price_contract import backfill_benchmark
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO market_index (index_code, index_name, date, "
                     "current_value) VALUES ('000001','上证指数','2026-10-26',4000.0)")
    result = backfill_benchmark(dry_run=False)
    assert "000001" in result["backfilled"]
    with db_mod.db_conn() as conn:
        row = conn.execute("SELECT close FROM benchmark_daily WHERE code='000001'").fetchone()
    assert row["close"] == 4000.0


def test_backfill_trading_calendar(db):
    from scripts.fix_price_contract import backfill_trading_calendar
    db_mod.KlineDAO().upsert_many("600519", [
        {"trade_date": d, "open": 1.0, "close": 1.0, "high": 1.0, "low": 1.0,
         "volume": 1.0, "amount": 1.0, "turnover": 0.1}
        for d in ("2026-10-26", "2026-10-27")])
    result = backfill_trading_calendar(dry_run=False)
    assert result["trading_days"] == 2
    with db_mod.db_conn() as conn:
        rows = conn.execute(
            "SELECT trade_date, is_open FROM trading_calendar ORDER BY trade_date"
        ).fetchall()
    assert rows[0]["is_open"] == 1
    assert any(r["is_open"] == 0 for r in rows)


def test_coverage_report(db):
    from scripts.fix_price_contract import coverage
    db_mod.KlineDAO().upsert_many("600519", [{
        "trade_date": "2026-10-26", "open": 1.0, "close": 1.0, "high": 1.0,
        "low": 1.0, "volume": 1.0, "amount": 1.0, "turnover": 0.1}])
    result = coverage()
    assert result["kline_codes"] == 1
    assert 0.0 <= result["coverage"] <= 1.0
