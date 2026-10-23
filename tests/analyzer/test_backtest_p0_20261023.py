"""P0-8 look-ahead-safe 回测回归测试（2026-10-23）。

- fundamentals_at：notice_date > 当日 → 不可用（防前视）
- run_backtest：基本可用，产出总收益
"""

import sqlite3

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "b.db"))
    db_mod.init_database()
    return db_mod


def _conn(tmp_path):
    return sqlite3.connect(str(tmp_path / "b.db"))


def _kline(code, dates):
    from src.models.database import KlineDAO
    KlineDAO().upsert_many(code, [{
        "trade_date": d, "open": c, "close": c, "high": c, "low": c,
        "volume": 0, "amount": 0, "turnover": 0,
    } for d, c in dates])


def _report(code, notice="2024-01-10", eps=1.0, roe=12.0):
    from src.models.database import FinancialHistoryDAO
    FinancialHistoryDAO().batch_save([{
        "stock_code": code, "report_date": "2023-12-31", "notice_date": notice,
        "roe": roe, "eps": eps, "gross_margin": 20.0, "net_margin": 10.0,
        "debt_ratio": 40.0, "fcf": 1e8,
    }])


def test_fundamentals_at_lookahead(db, tmp_path):
    from scripts import backtest_topk as bt
    _report("000001", notice="2025-04-01")
    conn = _conn(tmp_path)
    fund = bt.load_fundamentals(conn)
    assert bt.fundamentals_at(fund, "000001", "2025-01-01") is None
    assert bt.fundamentals_at(fund, "000001", "2025-05-01") is not None


def test_run_backtest_smoke(db, tmp_path):
    from scripts import backtest_topk as bt
    dates = [(f"2024-{m:02d}-02", 10.0 + m) for m in range(1, 13)]
    _kline("000001", dates)
    _report("000001", notice="2024-01-10")
    conn = _conn(tmp_path)
    res = bt.run_backtest(conn, "2024-01-01", "2024-12-31", topk=1, rebalance_freq="M")
    assert "error" not in res
    assert res["positions"] == 1
    # 价格从 11 涨到 22（10+m，m=1..12）→ 正收益
    assert res["total_return_pct"] > 0
