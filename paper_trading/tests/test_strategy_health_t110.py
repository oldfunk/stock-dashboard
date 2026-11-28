"""T110：策略健康度（I5a，只读）。"""
import sqlite3

import pytest

import paper_trading.dashboard as d
from paper_trading.strategy.health import classify, health_payload

DAYS = ("2026-11-20", "2026-11-23", "2026-11-24",
        "2026-11-25", "2026-11-26", "2026-11-27")
NAVS = (100000, 101000, 100500, 102000, 103000, 102000)
BENCH = (4000, 4020, 4010, 4040, 4060, 4050)


def _acct(tmp_path):
    db = tmp_path / "acct.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL,"
              " initial_cash REAL, created_at TEXT)")
    c.execute("INSERT INTO account (cash, initial_cash, created_at)"
              " VALUES (100000,100000,'x')")
    c.execute("CREATE TABLE nav_history (id INTEGER PRIMARY KEY, timestamp TEXT,"
              " cash REAL, market_value REAL, total_value REAL, pnl REAL,"
              " pnl_pct REAL)")
    for day, tv in zip(DAYS, NAVS):
        c.execute("INSERT INTO nav_history (timestamp,cash,market_value,"
                  "total_value,pnl,pnl_pct) VALUES (?,?,?,?,?,?)",
                  (day + "T20:50:00", tv, tv, tv, 0, 0))
    c.execute("CREATE TABLE fills (fill_id INTEGER PRIMARY KEY, symbol TEXT,"
              " direction INTEGER, volume INTEGER, price REAL, commission REAL,"
              " stamp_duty REAL, transfer_fee REAL, timestamp TEXT)")
    c.execute("INSERT INTO fills (symbol,direction,volume,price,commission,"
              "stamp_duty,transfer_fee,timestamp) VALUES (?,?,?,?,?,?,?,?)",
              ("600519", 1, 100, 10.0, 5.0, 0.0, 0.01,
               "2026-11-25T10:00:00"))
    c.commit()
    c.close()
    return str(db)


def _mother(tmp_path, closes=BENCH):
    root = tmp_path / "mother"
    db = root / "data" / "db" / "stock_dashboard.db"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE benchmark_daily (code TEXT, trade_date TEXT,"
              " close REAL)")
    for day, close in zip(DAYS, closes):
        c.execute("INSERT INTO benchmark_daily VALUES ('sh000300',?,?)",
                  (day, close))
    c.commit()
    c.close()
    return root


def test_health_metrics_and_healthy(tmp_path):
    out = health_payload(_acct(tmp_path), window=60, today="2026-11-28",
                         mother=_mother(tmp_path))
    assert out["available"] is True
    m = out["metrics"]
    assert m["portfolio_return"] == pytest.approx(0.02)
    assert m["benchmark_return"] == pytest.approx(0.0125)
    assert m["excess"] == pytest.approx(0.0075)
    assert m["excess_20"] == pytest.approx(0.0075)
    assert m["max_drawdown"] == pytest.approx(1 - 102000 / 103000, abs=1e-6)
    assert m["turnover"] == pytest.approx(1000 / (sum(NAVS) / 6), abs=1e-6)
    assert m["fee_drag"] == pytest.approx(5.01 / (sum(NAVS) / 6), abs=1e-8)
    assert out["health"] == "healthy" and out["recommendation"] == "active"


def test_health_demote_on_negative_excess(tmp_path):
    # 基准 +25%，组合 +2% ⇒ 20 日超额 ≈ -23%
    out = health_payload(_acct(tmp_path), today="2026-11-28",
                         mother=_mother(tmp_path, closes=(4000, 4200, 4400,
                                                          4600, 4800, 5000)))
    assert out["health"] == "demote" and out["recommendation"] == "observe"
    assert any("20日超额" in x for x in out["reasons"])


def test_health_unavailable_when_mother_missing(tmp_path):
    out = health_payload(_acct(tmp_path), today="2026-11-28",
                         mother=tmp_path / "nope")
    assert out["available"] is False
    assert "benchmark_daily" in out["reason"]


def test_classify_boundaries():
    assert classify(-0.06, 0.02, 0.001)[0] == "demote"
    assert classify(0.01, 0.16, 0.001)[0] == "demote"
    assert classify(0.01, 0.02, 0.02)[0] == "demote"
    assert classify(-0.01, 0.02, 0.001)[0] == "watch"
    assert classify(0.01, 0.11, 0.001)[0] == "watch"
    assert classify(0.01, 0.02, 0.001)[0] == "healthy"


def test_strategy_health_endpoint_page_gate():
    assert "/api/strategy-health" in d.PAGE and "策略健康" in d.PAGE
