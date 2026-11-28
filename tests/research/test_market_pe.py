"""市场 PE 历史：PIT 年报、正 PE 中位数、5 年分位。"""
from __future__ import annotations

import sqlite3

import pytest

from src.research.market_pe import (compute_history, percentile_5y,
                                    save_history)


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE financial_history (stock_code TEXT, report_type TEXT,
      notice_date TEXT, eps REAL);
    CREATE TABLE kline_daily (code TEXT, trade_date TEXT, close_raw REAL,
      close REAL);
    CREATE TABLE market_pe_history (trade_date TEXT PRIMARY KEY,
      pe_median REAL, sample_size INTEGER, coverage REAL, created_at TEXT);
    """)
    c.executemany("INSERT INTO financial_history VALUES (?,?,?,?)", [
        ("000001", "A", "2021-01-01", 1.0),
        ("000002", "A", "2021-01-01", 2.0),
        ("000003", "A", "2026-12-01", 1.0),   # 尚未披露，不能参与
    ])
    c.executemany("INSERT INTO kline_daily VALUES (?,?,?,?)", [
        ("000001", "2026-11-25", 10.0, None),
        ("000001", "2026-11-26", 20.0, None),
        ("000002", "2026-11-25", 10.0, None),
        ("000002", "2026-11-26", 10.0, None),
        ("000003", "2026-11-25", 99.0, None),
    ])
    return c


def test_compute_history_uses_latest_noticed_eps():
    c = _conn()
    rows = compute_history(c, "2026-11-20", "2026-11-30", active_count=10)
    by = {r["trade_date"]: r for r in rows}
    assert by["2026-11-25"]["pe_median"] == pytest.approx(7.5)
    assert by["2026-11-25"]["sample_size"] == 2
    assert by["2026-11-26"]["pe_median"] == pytest.approx(12.5)
    c.close()


def test_percentile_needs_min_samples():
    c = _conn()
    rows = compute_history(c, "2026-11-20", "2026-11-30", active_count=10)
    save_history(c, rows)
    assert percentile_5y(c, "2026-11-26", min_samples=1) == 100.0
    assert percentile_5y(c, "2026-11-26", min_samples=10) is None
    c.close()
