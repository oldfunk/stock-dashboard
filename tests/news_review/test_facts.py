"""F 线事实底座：纯函数与只读查询（内存库，不碰生产）。"""
from __future__ import annotations

import sqlite3

import pytest

from src.news_review import facts


def test_pct_handles_missing():
    assert facts.pct(110, 100) == pytest.approx(0.1)
    assert facts.pct(None, 100) is None
    assert facts.pct(110, 0) is None


def test_breadth_counts_and_drops_missing_prev():
    out = facts.breadth_from_pairs([(10, 9), (9, 10), (9, 9), (9, None)])
    assert out == {"up": 1, "down": 1, "flat": 1, "total": 3,
                   "up_pct": pytest.approx(1 / 3)}


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE benchmark_daily (code TEXT, trade_date TEXT, close REAL);
    CREATE TABLE daily_close (code TEXT, trade_date TEXT, close REAL, amount REAL);
    """)
    conn.executemany("INSERT INTO benchmark_daily VALUES (?,?,?)", [
        ("sh000300", "2026-11-26", 3900.0),
        ("sh000300", "2026-11-27", 4063.0),
    ])
    conn.executemany("INSERT INTO daily_close VALUES (?,?,?,?)", [
        ("000792", "2026-11-26", 10.0, 100.0),
        ("000792", "2026-11-27", 11.0, 120.0),
        ("600519", "2026-11-26", 20.0, 200.0),
        ("600519", "2026-11-27", 19.0, 180.0),
    ])
    return conn


def test_market_facts_indices_turnover_breadth():
    conn = _conn()
    out = facts.market_facts(conn, "2026-11-27")
    conn.close()
    assert out["asof"] == "2026-11-27"
    assert out["prev_date"] == "2026-11-26"
    csi = [i for i in out["indices"] if i["code"] == "sh000300"][0]
    assert csi["close"] == 4063.0
    assert csi["pct"] == pytest.approx(4063 / 3900 - 1)
    assert out["turnover"]["cur_yi"] == pytest.approx(0.03)
    assert out["breadth"]["up"] == 1 and out["breadth"]["down"] == 1


def test_market_facts_falls_back_to_latest_available_date():
    conn = _conn()
    out = facts.market_facts(conn, "2026-11-30")   # 周末/非交易日
    conn.close()
    assert out["asof"] == "2026-11-27"
