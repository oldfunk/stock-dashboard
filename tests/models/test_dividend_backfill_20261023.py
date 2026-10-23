"""P0-1 股息率回填回归测试（2026-10-23）。

- 股息率 = 每股现金分红 / 现价 × 100；分红率 = 每股现金分红 / EPS × 100
- 缺价/缺分红保持 NULL（不零填）；幂等
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "d.db"))
    db_mod.init_database()
    return db_mod


def _snap(code="000001", name="平安银行", price=10.0, **over):
    base = {
        "code": code, "name": name, "market": "A", "sector": "银行",
        "pe": 8.0, "pb": 0.9, "ps": 1.0, "market_cap": 1000.0,
        "circulating_cap": 900.0, "roe": 12.0, "revenue": 100.0,
        "revenue_growth": 5.0, "profit": 20.0, "profit_growth": 6.0,
        "debt_ratio": 90.0, "dividend_yield": None, "current_price": price,
        "high_52w": 12.0, "low_52w": 9.0, "is_st": False,
        "list_date": "2000-01-01", "snapshot_date": "2026-10-23",
    }
    base.update(over)
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([base])


def _div(code="000001", report_date="2025-12-31", cash=0.5, eps=1.0):
    from src.models.database import db_conn
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dividend_annual "
            "(stock_code, report_date, cash_per_share, eps, source, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (code, report_date, cash, eps, "test", "2026-10-23T00:00:00"),
        )


def _row(code):
    from src.models.database import db_conn
    with db_conn() as conn:
        return conn.execute(
            "SELECT dividend_yield, payout_ratio, dividend_year "
            "FROM stock_snapshot WHERE code=?", (code,)
        ).fetchone()


def test_backfill_computes_yield_and_payout(db):
    _snap(price=10.0)
    _div(cash=0.5, eps=1.0)
    from src.collector.dividend_fetcher import backfill_snapshot_dividends
    stats = backfill_snapshot_dividends("2025-12-31")
    assert stats["with_yield"] == 1 and stats["with_payout"] == 1
    row = _row("000001")
    assert row["dividend_yield"] == 5.0      # 0.5 / 10 * 100
    assert row["payout_ratio"] == 50.0       # 0.5 / 1.0 * 100
    assert row["dividend_year"] == "2025"


def test_missing_price_keeps_yield_null(db):
    _snap(code="000002", price=None)
    _div(code="000002")
    from src.collector.dividend_fetcher import backfill_snapshot_dividends
    backfill_snapshot_dividends("2025-12-31")
    row = _row("000002")
    assert row["dividend_yield"] is None      # 缺价 → 不零填
    assert row["payout_ratio"] == 50.0        # 分红率不依赖价格


def test_no_dividend_keeps_null(db):
    _snap(code="000003", price=10.0)
    from src.collector.dividend_fetcher import backfill_snapshot_dividends
    backfill_snapshot_dividends("2025-12-31")
    row = _row("000003")
    assert row["dividend_yield"] is None
    assert row["payout_ratio"] is None
    assert row["dividend_year"] is None


def test_backfill_idempotent(db):
    _snap(price=10.0)
    _div()
    from src.collector.dividend_fetcher import backfill_snapshot_dividends
    s1 = backfill_snapshot_dividends("2025-12-31")
    s2 = backfill_snapshot_dividends("2025-12-31")
    assert s1["with_yield"] == s2["with_yield"] == 1
    assert _row("000001")["dividend_yield"] == 5.0
