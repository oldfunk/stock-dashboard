"""K 线覆盖报告（A3）纯函数 + 夹具库（不读生产库）。"""
from __future__ import annotations

import sqlite3

import pytest

from scripts import kline_coverage_report as rep

SCHEMA = """
CREATE TABLE stock_snapshot (code TEXT PRIMARY KEY, name TEXT, market TEXT);
CREATE TABLE kline_daily (code TEXT, trade_date TEXT, close REAL,
                          PRIMARY KEY (code, trade_date));
CREATE TABLE daily_close (code TEXT, trade_date TEXT, close REAL,
                          PRIMARY KEY (code, trade_date));
CREATE TABLE screening_result (run_id TEXT, run_date TEXT, code TEXT,
                               name TEXT, score REAL);
"""


def test_board_classifies_prefixes():
    assert rep.board("600519") == "沪主板"
    assert rep.board("000792") == "深主板"
    assert rep.board("300750") == "创业板"
    assert rep.board("688981") == "科创板"
    assert rep.board("920156") == "北交所"


def test_coverage_counts_and_missing_buckets():
    active = ["600001", "600002", "000001", "300001", "920001"]
    kline = {"600001", "000001", "300001", "999999"}  # 999999 不在活跃池
    daily = set(active) | {"999999"}
    cov = rep.coverage(active, kline, daily)
    assert cov["active"] == 5
    assert cov["kline"] == 3
    assert cov["kline_pct"] == pytest.approx(0.6)
    assert cov["daily_close"] == 5
    assert cov["missing"] == 2
    assert cov["missing_by_board"] == {"北交所": 1, "沪主板": 1}
    assert cov["missing_sample"] == ["600002", "920001"]


def test_target_status_requires_exact_date():
    targets = [{"code": "792", "name": "盐湖", "roles": ["持仓", "候选"]},
               {"code": "600211", "name": "西藏药业", "roles": ["候选"]}]
    rows = rep.target_status(targets, {"000792": "2026-11-27"}, "2026-11-27")
    assert rows[0]["code"] == "000792"
    assert rows[0]["fresh"] is True
    assert rows[1]["last_date"] is None
    assert rows[1]["fresh"] is False


def test_render_flags_missing_and_lists_board():
    report = {
        "date": "2026-11-27",
        "coverage": {"active": 4, "kline": 2, "kline_pct": 0.5,
                     "daily_close": 4, "daily_close_pct": 1.0,
                     "missing": 2,
                     "missing_by_board": {"沪主板": 2},
                     "missing_sample": ["600001", "600002"]},
        "targets": [{"code": "000792", "name": "盐湖", "roles": ["持仓"],
                     "last_date": "2026-11-27", "fresh": True},
                    {"code": "600211", "name": "西藏药业", "roles": ["候选"],
                     "last_date": "2026-11-26", "fresh": False}],
        "trend": [],
        "missing_limit": 1,
    }
    text = rep.render(report)
    assert "1/2" in text
    assert "| 600211 | 西藏药业 | 候选 | 2026-11-26 | 缺 |" in text
    assert "沪主板：2" in text
    assert "600001" in text and "600002" not in text.split("样例：")[1]


def test_collect_with_fixture(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO stock_snapshot VALUES (?,?,?)", [
        ("000792", "盐湖股份", "A"), ("600211", "西藏药业", "A"),
        ("300001", "特锐德", "A")])
    conn.executemany("INSERT INTO kline_daily VALUES (?,?,?)", [
        ("000792", "2026-11-27", 21.5), ("000792", "2026-11-26", 21.4),
        ("600211", "2026-11-26", 45.0), ("300001", "2026-11-27", 20.0)])
    conn.executemany("INSERT INTO daily_close VALUES (?,?,?)", [
        ("000792", "2026-11-27", 21.5), ("600211", "2026-11-27", 44.0),
        ("300001", "2026-11-27", 20.0)])
    conn.execute("INSERT INTO screening_result VALUES "
                 "('20261127_153012', '2026-11-27', '600211', '西藏药业', 88)")
    monkeypatch.setattr(rep, "load_positions", lambda: {
        "data": [{"symbol": "000792", "total_volume": 400}]})
    report = rep.collect(conn, days=2)
    conn.close()
    assert report["date"] == "2026-11-27"
    cov = report["coverage"]
    assert cov["active"] == 3 and cov["kline"] == 2 and cov["missing"] == 1
    assert {t["code"]: t["fresh"] for t in report["targets"]} == {
        "000792": True, "600211": False}
    assert [r["date"] for r in report["trend"]] == [
        "2026-11-26", "2026-11-27"]
