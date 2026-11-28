"""R3-4：FIFO 已实现 + 周期归因（夹具库/假桥）。"""
from __future__ import annotations

import pytest

from src.research import attribution


def test_fifo_realized_only_counts_period_sells():
    fills = [
        {"symbol": "000792", "direction": 1, "volume": 100, "price": 20.0,
         "timestamp": "2026-11-01T09:30:00"},
        {"symbol": "000792", "direction": -1, "volume": 100, "price": 22.0,
         "timestamp": "2026-11-10T09:30:00"},
        {"symbol": "000792", "direction": 1, "volume": 100, "price": 23.0,
         "timestamp": "2026-11-11T09:30:00"},
        {"symbol": "000792", "direction": -1, "volume": 100, "price": 21.0,
         "timestamp": "2026-11-20T09:30:00"},
    ]
    assert attribution.fifo_realized(fills, "2026-11-15", "2026-11-30") == {
        "000792": -200.0}


def test_attribute_period_combines_nav_benchmark_costs(minimal_db, monkeypatch):
    from src.models import database as db_mod
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO benchmark_daily VALUES "
                     "('sh000300','2026-11-20',100.0)")
        conn.execute("INSERT INTO benchmark_daily VALUES "
                     "('sh000300','2026-11-27',110.0)")
        conn.execute("INSERT INTO stock_snapshot (code,name,market,sector,"
                     "snapshot_date) VALUES ('000792','盐湖','A','化工','2026-11-27')")
        conn.execute("INSERT INTO market_state_daily (trade_date,metrics_json,"
                     "coverage,status,regime,created_at) VALUES "
                     "('2026-11-27','{}',1.0,'pass','risk_off','x')")
    import src.paper_bridge as pb
    monkeypatch.setattr(pb, "load_nav", lambda limit=1000: {"source": "paper:nav",
        "data": [{"timestamp": "2026-11-20T15:00:00", "total_value": 100000},
                 {"timestamp": "2026-11-27T15:00:00", "total_value": 105000}]})
    monkeypatch.setattr(pb, "load_recent_fills", lambda limit=1000: {
        "source": "paper:fills", "data": [
            {"symbol": "000792", "direction": -1, "volume": 100, "price": 22.0,
             "timestamp": "2026-11-24T09:30:00", "commission": 5.0,
             "stamp_duty": 0.2, "transfer_fee": 0.02},
            {"symbol": "000792", "direction": 1, "volume": 100, "price": 20.0,
             "timestamp": "2026-11-20T09:30:00", "commission": 0,
             "stamp_duty": 0, "transfer_fee": 0}]})
    out = attribution.attribute_period("2026-11-20", "2026-11-27", "sh000300", "D1")
    assert out["total_return"] == pytest.approx(0.05)
    assert out["excess_return"] == pytest.approx(0.05 - 0.10)
    assert out["costs"] == pytest.approx(5.22)
    assert out["factor"]["fills"] == 2
    assert out["stock"]["000792"] == pytest.approx(200.0)
    assert out["sector"]["化工"] == pytest.approx(200.0)
    assert out["regime"] == "risk_off"
