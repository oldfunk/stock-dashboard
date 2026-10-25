"""P0-2 分红历史 / 连续分红年数回归测试（2026-10-23）。

- 从最新年报年倒推，必须有当年分红且 >0、年份连续才累加；中断即停
- 回填 stock_snapshot.dividend_years（无连续分红置 NULL）
"""

import pytest
from fastapi.testclient import TestClient

from src.models import database as db_mod
from src.collector import dividend_fetcher as df


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "h.db"))
    db_mod.init_database()
    return db_mod


def _div(code, report_date, cash):
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dividend_annual "
            "(stock_code, report_date, cash_per_share, eps, source, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (code, report_date, cash, 1.0, "test", "2026-10-23T00:00:00"),
        )


def _snap(code, name="平安银行"):
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([{
        "code": code, "name": name, "market": "A", "sector": "银行",
        "pe": 8.0, "pb": 0.9, "ps": 1.0, "market_cap": 1000.0,
        "circulating_cap": 900.0, "roe": 12.0, "revenue": 100.0,
        "revenue_growth": 5.0, "profit": 20.0, "profit_growth": 6.0,
        "debt_ratio": 90.0, "current_price": 10.0,
        "high_52w": 12.0, "low_52w": 9.0, "is_st": False,
        "list_date": "2000-01-01", "snapshot_date": "2026-10-23",
    }])


def _row(code, col):
    with db_mod.db_conn() as conn:
        return conn.execute(
            f"SELECT {col} FROM stock_snapshot WHERE code=?", (code,)
        ).fetchone()[0]


def test_consecutive_count_stops_at_gap(db, monkeypatch):
    monkeypatch.setattr(df, "latest_annual_report_date", lambda: "2025-12-31")
    _div("000001", "2025-12-31", 0.1)
    _div("000001", "2024-12-31", 0.1)
    _div("000001", "2023-12-31", 0.1)
    _div("000001", "2021-12-31", 0.1)  # 2022 缺 → 中断
    assert df.compute_dividend_years()["000001"] == 3


def test_break_on_zero_dividend(db, monkeypatch):
    monkeypatch.setattr(df, "latest_annual_report_date", lambda: "2025-12-31")
    _div("000002", "2025-12-31", 0.1)
    _div("000002", "2024-12-31", 0.0)  # 不分红 → 中断
    _div("000002", "2023-12-31", 0.1)
    assert df.compute_dividend_years()["000002"] == 1


def test_latest_record_within_one_year_counts(db, monkeypatch):
    """最新分红在 2024（2025 未披露/未分红）→ 以 2024 为起点计算，不整列 NULL。"""
    monkeypatch.setattr(df, "latest_annual_report_date", lambda: "2025-12-31")
    _div("000003", "2024-12-31", 0.1)
    _div("000003", "2023-12-31", 0.1)
    assert df.compute_dividend_years()["000003"] == 2


def test_stale_record_beyond_one_year_is_ignored(db, monkeypatch):
    """分红记录超过一年未更新（2023 vs 最新 2025）→ 不拿来充数。"""
    monkeypatch.setattr(df, "latest_annual_report_date", lambda: "2025-12-31")
    _div("000004", "2023-12-31", 0.1)
    assert "000004" not in df.compute_dividend_years()


def test_backfill_sets_null_for_others(db, monkeypatch):
    monkeypatch.setattr(df, "latest_annual_report_date", lambda: "2025-12-31")
    _snap("000001")
    _snap("000002", "万科A")
    _div("000001", "2025-12-31", 0.1)
    stats = df.backfill_dividend_years()
    assert stats["stocks"] == 1
    assert _row("000001", "dividend_years") == 1
    assert _row("000002", "dividend_years") is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "w.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_detail_shows_dividend_years(client):
    _snap("000792")
    with db_mod.db_conn() as conn:
        conn.execute("UPDATE stock_snapshot SET dividend_years=8 WHERE code='000792'")
    html = client.get("/stock/000792").text
    assert "连分年数" in html
    assert ">8<" in html
