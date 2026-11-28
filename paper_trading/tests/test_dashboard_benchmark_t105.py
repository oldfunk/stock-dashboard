"""T105：CSI300 基准曲线（服务端对齐/归一化/超额/stale）。"""
import sqlite3

import pytest

import paper_trading.dashboard as d


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
    for day, tv in (("2026-11-20", 100000.0), ("2026-11-23", 101000.0),
                    ("2026-11-24", 102000.0)):
        c.execute("INSERT INTO nav_history (timestamp,cash,market_value,"
                  "total_value,pnl,pnl_pct) VALUES (?,?,?,?,?,?)",
                  (day + "T20:50:00", tv, tv, tv, 0, 0))
    c.commit()
    c.close()
    return str(db)


def _mother(tmp_path, rows):
    m = tmp_path / "mother"
    p = m / "data" / "db"
    p.mkdir(parents=True)
    c = sqlite3.connect(p / "stock_dashboard.db")
    c.execute("CREATE TABLE benchmark_daily (code TEXT, trade_date TEXT,"
              " close REAL)")
    for code, day, close in rows:
        c.execute("INSERT INTO benchmark_daily VALUES (?,?,?)", (code, day, close))
    c.commit()
    c.close()
    return m


def test_benchmark_aligned_and_excess(tmp_path):
    acct = _acct(tmp_path)
    m = _mother(tmp_path, [("sh000300", "2026-11-20", 4000.0),
                           ("sh000300", "2026-11-23", 4040.0),
                           ("sh000300", "2026-11-24", 4000.0)])
    out = d._benchmark_payload(acct, today="2026-11-24", mother=m)
    assert out["available"] is True and out["code"] == "sh000300"
    assert out["stale"] is False
    assert [p["date"] for p in out["series"]] == [
        "2026-11-20", "2026-11-23", "2026-11-24"]
    last = out["series"][-1]
    assert last["portfolio"] == pytest.approx(0.02)
    assert last["benchmark"] == pytest.approx(0.0)
    assert last["excess"] == pytest.approx(0.02)
    assert out["summary"]["excess"] == pytest.approx(0.02)
    assert out["summary"]["benchmark_mdd"] == pytest.approx(1 - 4000 / 4040,
                                                           abs=1e-6)
    assert out["window"]["days"] == 3


def test_benchmark_stale_flag(tmp_path):
    acct = _acct(tmp_path)
    m = _mother(tmp_path, [("sh000300", "2026-11-20", 4000.0),
                           ("sh000300", "2026-11-23", 4010.0),
                           ("sh000300", "2026-11-24", 4020.0)])
    out = d._benchmark_payload(acct, today="2026-12-01", mother=m)
    assert out["available"] is True and out["stale"] is True


def test_benchmark_unavailable_when_mother_missing(tmp_path):
    acct = _acct(tmp_path)
    out = d._benchmark_payload(acct, today="2026-11-24",
                               mother=tmp_path / "nope")
    assert out["available"] is False
    assert "benchmark_daily" in out["reason"]


def test_benchmark_needs_two_common_days(tmp_path):
    acct = _acct(tmp_path)
    m = _mother(tmp_path, [("sh000300", "2026-11-24", 4000.0)])
    out = d._benchmark_payload(acct, today="2026-11-24", mother=m)
    assert out["available"] is False
    assert "共同交易日" in out["reason"]
