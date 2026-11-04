"""T26 每日 5 分钟线积累：周期标签、幂等、缺口报告、保留窗口。

全部离线：`sync_day` 的 `fetch` 注入假源，不打网络、不碰生产库。
"""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from paper_trading.data import minute as _min
from paper_trading.data.db_manager import DataDBManager

DAY = "2026-10-28"


def rows_for(day, slots, *, close=10.0):
    return [{"ts": f"{day}T{s}", "open": close, "high": close + 0.1,
             "low": close - 0.1, "close": close, "volume": 1000, "amount": 1e4}
            for s in slots]


@pytest.fixture()
def db(tmp_path):
    return DataDBManager(tmp_path / "data.db")


# ---------- 周期网格 ----------
def test_expected_slots_per_period():
    assert len(_min.expected_slots("1")) == 240
    assert len(_min.expected_slots("5")) == 48        # 与生产实测 48 根/标的/日一致
    assert len(_min.expected_slots("15")) == 16
    assert len(_min.expected_slots("30")) == 8
    assert _min.expected_slots("5")[0] == "09:35" and _min.expected_slots("5")[-1] == "15:00"


# ---------- schema 迁移与周期标签 ----------
def test_legacy_minute_rows_get_period_label(tmp_path):
    p = tmp_path / "data.db"
    conn = sqlite3.connect(p)
    conn.execute("""CREATE TABLE minute_bars (symbol TEXT NOT NULL, ts TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL DEFAULT 0, amount REAL DEFAULT 0,
                    PRIMARY KEY (symbol, ts))""")
    conn.execute("INSERT INTO minute_bars VALUES ('000792','2026-10-28T15:00',1,1,1,1,1,1)")
    conn.commit()
    conn.close()
    d = DataDBManager(p)                                # 打开即迁移
    got = d.get_minute_bars("000792", period="5")
    assert len(got) == 1 and got[0]["period"] == "5"    # 现网全部来自 period=5 路径
    assert got[0]["adjust"] == "raw"


def test_period_isolates_1min_from_5min(db):
    db.upsert_minute_bars("000792", rows_for(DAY, ["09:31", "09:32"]), period="1")
    db.upsert_minute_bars("000792", rows_for(DAY, ["09:35"]), period="5")
    assert {r["ts"] for r in db.get_minute_bars("000792", period="1")} == \
        {f"{DAY}T09:31", f"{DAY}T09:32"}
    assert {r["ts"] for r in db.get_minute_bars("000792", period="5")} == {f"{DAY}T09:35"}
    assert len(db.get_minute_bars("000792")) == 3       # 不带 period 仍看全量


def test_upsert_minute_is_idempotent(db):
    db.upsert_minute_bars("000792", rows_for(DAY, ["09:35", "09:40"]), period="5")
    db.upsert_minute_bars("000792", rows_for(DAY, ["09:35", "09:40"], close=11.0), period="5")
    got = db.get_minute_bars("000792", period="5")
    assert len(got) == 2 and all(abs(r["close"] - 11.0) < 1e-9 for r in got)


# ---------- sync_day ----------
def test_sync_day_writes_only_target_day_and_reports_gaps(db):
    full = _min.expected_slots("5")
    def fake(sym, period, days, now):
        return rows_for(DAY, full[:-3], close=10.0) + rows_for("2026-10-27", full)
    res = _min.sync_day(db, ["000792"], day=DAY, period="5", fetch=fake, gap=0)
    assert res["written"] == 45                          # 只落目标日
    assert res["expected_per_symbol"] == 48
    assert res["gapped"] == ["000792"]
    assert res["per_symbol"]["000792"]["missing"] == 3
    assert db.minute_coverage("5")["symbols"] == 1


def test_sync_day_is_repeatable(db):
    full = _min.expected_slots("5")
    def fake(sym, period, days, now):
        return rows_for(DAY, full)
    first = _min.sync_day(db, ["000792", "600519"], day=DAY, fetch=fake, gap=0)
    again = _min.sync_day(db, ["000792", "600519"], day=DAY, fetch=fake, gap=0)
    assert first["written"] == again["written"] == 96
    assert again["gapped"] == [] and again["no_data"] == []
    assert db.minute_coverage("5")["rows"] == 96         # 重跑不翻倍


def test_sync_day_reports_fetch_error_without_killing_batch(db):
    def fake(sym, period, days, now):
        if sym == "600519":
            raise TimeoutError("sina stall")
        return rows_for(DAY, _min.expected_slots("5"))
    res = _min.sync_day(db, ["000792", "600519"], day=DAY, fetch=fake, gap=0)
    assert res["errors"] == ["600519"] and res["written"] == 48
    assert "TimeoutError" in res["per_symbol"]["600519"]["error"]


def test_sync_day_flags_symbol_with_no_data(db):
    res = _min.sync_day(db, ["000792"], day=DAY, fetch=lambda s, p, d, n: [], gap=0)
    assert res["written"] == 0 and res["no_data"] == ["000792"]


# ---------- 保留窗口 ----------
def test_prune_keeps_recent_days_only(db):
    for i in range(6):
        d = (date(2026, 10, 20) + timedelta(days=i)).isoformat()
        db.upsert_minute_bars("000792", rows_for(d, ["09:35", "15:00"]), period="5")
    keep_days = db.minute_days(period="5")[-3:]
    n = db.prune_minute_bars(keep_days[0], period="5")
    assert n == 6
    assert db.minute_days(period="5") == keep_days
    assert db.minute_coverage("5")["rows"] == 6


def test_prune_does_not_cross_periods(db):
    db.upsert_minute_bars("000792", rows_for("2026-01-05", ["09:35"]), period="5")
    db.upsert_minute_bars("000792", rows_for("2026-01-05", ["09:31"]), period="1")
    db.prune_minute_bars("2026-01-06", period="5")
    assert db.minute_coverage("5")["rows"] == 0
    assert db.minute_coverage("1")["rows"] == 1          # 别的周期不受牵连
