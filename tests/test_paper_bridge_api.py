"""纸盘只读 API（E1）：状态聚合、块级降级、limit 校验（夹具库，不碰生产）。"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.paper_bridge.api import router

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
def client(tmp_path, monkeypatch):
    db = tmp_path / "paper_account.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO account VALUES (1, 82399.31, 100000, "
                "'2026-09-28T13:36:50')")
    con.execute("INSERT INTO positions VALUES "
                "('000792', 400, 400, 23.98, '2026-11-27T15:00:00', '2026-10-08', 25.0)")
    con.execute("INSERT INTO agent_plans (plan_date, symbols, plan_json, status, "
                "created_at) VALUES ('2026-11-30', '000792', '{\"actions\": []}', "
                "'pending', '2026-11-27T16:41:38')")
    con.execute("INSERT INTO nav_history VALUES (1, '2026-11-27T15:00:00', "
                "82399.31, 14726.0, 97125.31, 82399.31, -2874.69, -0.0287)")
    con.execute("INSERT INTO fills VALUES (1, 7, '000848', 1, 100, 7.4, 5.0, "
                "0.0, 0.0074, '2026-11-27T09:30:34')")
    con.commit()
    con.close()
    monkeypatch.setenv("PAPER_BRIDGE_DATA_DB", str(db))
    monkeypatch.setenv("PAPER_BRIDGE_ACCOUNT_DB", str(db))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_status_aggregates_blocks(client):
    body = client.get("/api/paper/status").json()
    assert body["ok"] is True
    data = body["data"]
    assert data["available"] is True
    assert data["account"]["source"] == "paper:account"
    assert data["account"]["data"]["cash"] == pytest.approx(82399.31)
    assert len(data["positions"]["data"]) == 1
    assert data["plan"]["data"]["id"] == 1
    assert data["plan"]["data"]["plan_date"] == "2026-11-30"
    assert data["nav"]["data"][0]["total_value"] == pytest.approx(97125.31)
    assert data["fills"]["data"][0]["symbol"] == "000848"


def test_status_degrades_when_db_missing(client, tmp_path, monkeypatch):
    missing = tmp_path / "nope.db"
    monkeypatch.setenv("PAPER_BRIDGE_DATA_DB", str(missing))
    monkeypatch.setenv("PAPER_BRIDGE_ACCOUNT_DB", str(missing))
    body = client.get("/api/paper/status")
    assert body.status_code == 200
    data = body.json()["data"]
    assert data["available"] is False
    assert data["account"]["source"] == "unavailable"


def test_nav_limit_and_validation(client):
    assert client.get("/api/paper/nav?limit=1").json()["data"]["data"][0]["total_value"] \
        == pytest.approx(97125.31)
    assert client.get("/api/paper/nav?limit=0").status_code == 422
