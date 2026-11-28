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


def test_portfolio_facts_prefers_panel_actual_returns(monkeypatch):
    import src.paper_bridge as pb
    monkeypatch.setattr(pb, "load_panel_snapshot", lambda **k: {
        "available": True, "source": "paper:panel", "asof": "2026-11-27",
        "performance": {"total_return": -0.0287, "total_pnl": -2874.68,
                        "cash": 82399.32, "market_value": 14726.0,
                        "total_value": 97125.32, "realized_pnl": -2859.54,
                        "fees_total": 40.68, "mdd": 0.0417, "exposure": 0.15},
        "today": {"today_pnl": 811.99},
        "positions": [{"symbol": "000792", "total_volume": 400,
                       "available_volume": 400, "avg_cost": 23.98,
                       "current_price": 22.64, "market_value": 9056.0}],
    })
    monkeypatch.setattr(pb, "load_status", lambda **k: {
        "plan": {"data": []}, "fills": {"data": []}})
    out = facts.portfolio_facts()
    assert out["source"] == "paper:panel"
    assert out["actual"]["total_return"] == -0.0287
    assert out["actual"]["realized_pnl"] == -2859.54
    assert out["positions"][0]["unrealized_pnl"] == pytest.approx(
        (22.64 - 23.98) * 400, abs=0.01)
    assert out["nav"]["total_value"] == 97125.32
