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
                     "current_value, timestamp) VALUES ('000001','上证指数','2026-10-26',4000.0,'2026-10-26T15:00:00')")
    result = backfill_benchmark(dry_run=False)
    assert "000001" in result["backfilled"]
    with db_mod.db_conn() as conn:
        row = conn.execute("SELECT close FROM benchmark_daily WHERE code='000001'").fetchone()
    assert row["close"] == 4000.0


def test_backfill_trading_calendar(db, monkeypatch):
    from scripts import fix_price_contract as fpc
    from scripts.fix_price_contract import backfill_trading_calendar
    # 源覆盖 10-27 ~ 11-06（含 10-31/11-01 周末非交易日窗口）
    monkeypatch.setattr(fpc, "fetch_trade_calendar", lambda: [
        "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
        "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"])
    db_mod.KlineDAO().upsert_many("600519", [
        {"trade_date": d, "open": 1.0, "close": 1.0, "high": 1.0, "low": 1.0,
         "volume": 1.0, "amount": 1.0, "turnover": 0.1}
        for d in ("2026-10-23", "2026-10-26")])
    result = backfill_trading_calendar(dry_run=False)
    assert result["trading_days"] == 2
    with db_mod.db_conn() as conn:
        rows = conn.execute(
            "SELECT trade_date, is_open FROM trading_calendar ORDER BY trade_date"
        ).fetchall()
    assert rows[0]["is_open"] == 1
    # 周末（10-24/25）不在 kline 中 -> is_open=0
    assert any(r["is_open"] == 0 for r in rows)
    # 未来段（Issue #8）：源内交易日=1，10-31/11-01 周末=0，覆盖到 11-06
    future = {r["trade_date"]: r["is_open"] for r in rows
              if r["trade_date"] > "2026-10-26"}
    assert future["2026-10-27"] == 1
    assert future["2026-10-31"] == 0
    assert future["2026-11-06"] == 1
    assert result["future"]["covered_to"] == "2026-11-06"
    assert result["future"]["trading_days"] == 9


def test_backfill_calendar_future_fail_closed(db, monkeypatch):
    """源拉取失败（回 []）：只写历史，未来段一行都不写。"""
    from scripts import fix_price_contract as fpc
    from scripts.fix_price_contract import backfill_trading_calendar
    monkeypatch.setattr(fpc, "fetch_trade_calendar", lambda: [])
    db_mod.KlineDAO().upsert_many("600519", [{
        "trade_date": "2026-10-26", "open": 1.0, "close": 1.0, "high": 1.0,
        "low": 1.0, "volume": 1.0, "amount": 1.0, "turnover": 0.1}])
    result = backfill_trading_calendar(dry_run=False)
    assert result["future"]["source"] is None
    with db_mod.db_conn() as conn:
        mx = conn.execute("SELECT MAX(trade_date) AS m FROM trading_calendar"
                         ).fetchone()["m"]
    assert mx == "2026-10-26"  # 无编造的未来行


def test_fetch_trade_calendar_parses_and_fails_closed(monkeypatch):
    """S15：新浪交易日历解析（排序/去非法日期）+ 失败回 []。"""
    import pandas as pd
    from src.collector import akshare_fetcher as f

    monkeypatch.setattr(f, "ak", type("A", (), {
        "tool_trade_date_hist_sina": staticmethod(lambda: pd.DataFrame(
            {"trade_date": ["2026-10-28", "2026-10-27", "not-a-date"]}))})())
    assert f.fetch_trade_calendar() == ["2026-10-27", "2026-10-28"]

    class Broken:
        @staticmethod
        def tool_trade_date_hist_sina():
            raise RuntimeError("network down")
    monkeypatch.setattr(f, "ak", Broken)
    assert f.fetch_trade_calendar() == []


def test_coverage_report(db):
    from scripts.fix_price_contract import coverage
    db_mod.KlineDAO().upsert_many("600519", [{
        "trade_date": "2026-10-26", "open": 1.0, "close": 1.0, "high": 1.0,
        "low": 1.0, "volume": 1.0, "amount": 1.0, "turnover": 0.1}])
    result = coverage()
    assert result["kline_codes"] == 1
    assert 0.0 <= result["coverage"] <= 1.0
