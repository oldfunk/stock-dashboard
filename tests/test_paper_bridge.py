"""纸盘桥 E0：只读、契约、失败降级（不依赖生产库）。"""
from __future__ import annotations

import sqlite3

import pytest

from src.paper_bridge import settings
from src.paper_bridge.conn import connect, missing_columns
from src.paper_bridge.state import (
    load_account,
    load_nav,
    load_pending_plan,
    load_positions,
    load_recent_fills,
)

SCHEMA = """
CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL, initial_cash REAL,
                      created_at TEXT);
CREATE TABLE positions (symbol TEXT PRIMARY KEY, total_volume INTEGER,
                        available_volume INTEGER, avg_cost REAL,
                        last_update TEXT, entry_day TEXT, peak_price REAL);
CREATE TABLE agent_plans (id INTEGER PRIMARY KEY, plan_date TEXT, symbols TEXT,
                          plan_json TEXT, status TEXT, created_at TEXT,
                          executed_at TEXT);
CREATE TABLE fills (fill_id INTEGER PRIMARY KEY, order_id INTEGER,
                    symbol TEXT, direction INTEGER, volume INTEGER,
                    price REAL, commission REAL, stamp_duty REAL,
                    transfer_fee REAL, timestamp TEXT);
CREATE TABLE nav_history (id INTEGER PRIMARY KEY, timestamp TEXT, cash REAL,
                          market_value REAL, total_value REAL,
                          available_cash REAL, pnl REAL, pnl_pct REAL);
"""


@pytest.fixture()
def acct(tmp_path):
    p = tmp_path / "paper_account.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO account (cash, initial_cash, created_at) "
                "VALUES (85589.37, 100000, '2026-09-28T13:36:50')")
    con.execute("INSERT INTO positions VALUES "
                "('000792', 400, 400, 23.98, '2026-11-20T15:00:00', '2026-10-08', 25.0)")
    con.execute("INSERT INTO positions VALUES "
                "('000426', 100, 0, 19.87, '2026-11-20T15:00:00', '2026-11-20', 19.9)")
    con.execute("INSERT INTO agent_plans (plan_date, symbols, plan_json, status, "
                "created_at) VALUES ('2026-11-23', '000792', '{\"actions\": [], "
                "\"summary\": \"hold\"}', 'pending', '2026-11-20T16:36:00')")
    con.execute("INSERT INTO fills VALUES (1, 1, '002192', -1, 200, 43.15, 5.0, "
                "4.315, 0.0, '2026-11-20T09:30:36')")
    con.execute("INSERT INTO nav_history VALUES (1, '2026-11-20T15:00:00', "
                "85589.37, 10278.0, 95867.37, 85589.37, -4132.63, -0.0413)")
    con.commit()
    con.close()
    return p


def _kwargs(acct):
    return {"data_db": acct, "account_db": acct}


def test_read_only_connection_rejects_write(acct):
    conn = connect(acct)
    assert conn is not None
    with pytest.raises(sqlite3.Error):
        conn.execute("INSERT INTO account (cash, initial_cash) VALUES (1, 1)")
    conn.close()


def test_account_and_positions(acct):
    acc = load_account(**_kwargs(acct))
    assert acc["source"] == "paper:account"
    assert acc["data"]["cash"] == pytest.approx(85589.37)
    pos = load_positions(**_kwargs(acct))
    assert pos["source"] == "paper:positions"
    assert [p["symbol"] for p in pos["data"]] == ["000426", "000792"]
    assert pos["data"][0]["available_volume"] == 0
    assert pos["asof"].startswith("2026-11-20")


def test_pending_plan_parses_json(acct):
    plan = load_pending_plan("2026-11-23", **_kwargs(acct))
    assert plan["source"] == "paper:plans"
    assert plan["data"]["id"]
    assert plan["data"]["plan"]["summary"] == "hold"
    assert "plan_json" not in plan["data"]


def test_nav_and_fills(acct):
    nav = load_nav(**_kwargs(acct))
    assert nav["data"][0]["total_value"] == pytest.approx(95867.37)
    assert nav["asof"].startswith("2026-11-20")
    fills = load_recent_fills(**_kwargs(acct))
    assert fills["data"][0]["direction"] == -1


def test_missing_db_is_unavailable(tmp_path):
    missing = tmp_path / "nope.db"
    out = load_account(data_db=missing, account_db=missing)
    assert out["source"] == "unavailable"
    assert "不存在" in out["reason"]


def test_missing_column_is_unavailable(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL)")
    con.execute("INSERT INTO account (cash) VALUES (1)")
    con.commit()
    con.close()
    out = load_account(data_db=p, account_db=p)
    assert out["source"] == "unavailable"
    assert "initial_cash" in out["reason"] or "读取失败" in out["reason"]


def test_contract_reports_missing_tables(acct):
    conn = connect(acct)
    assert missing_columns(conn) == {}
    conn.close()


def test_paths_default_to_merged_tree(monkeypatch):
    monkeypatch.delenv("PAPER_BRIDGE_ROOT", raising=False)
    monkeypatch.delenv("PAPER_BRIDGE_DATA_DB", raising=False)
    monkeypatch.delenv("PAPER_BRIDGE_ACCOUNT_DB", raising=False)
    data, account = settings.bridge_paths()
    assert data.name == "data.db" and data.parent.name == "paper_trading"
    assert account.name == "paper_account.db"
