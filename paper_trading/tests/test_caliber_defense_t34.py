"""T34 §5.5 裁决 1+2 落地测试：母库口径标签如实、当日跨源对照、T+1 翻转停线。

全部离线：母库用临时 sqlite（只读连接）、实时快照与日线都走注入参数。
"""
import sqlite3
from datetime import date, datetime

import pytest

from paper_trading.data import raw_feed
from paper_trading.data.db_manager import DataDBManager
from paper_trading.models import Bar

TODAY = date(2026, 10, 28)
CLOSED = datetime(2026, 10, 28, 15, 30)
IN_SESSION = datetime(2026, 10, 28, 10, 30)


def mk(sym, d, close, *, volume=1000, amount=1e7, adjust="raw"):
    return Bar(symbol=sym, timestamp=datetime.combine(d, datetime.min.time()),
               open=close, high=close + 0.1, low=close - 0.1, close=close,
               volume=volume, amount=amount, adjust=adjust)


@pytest.fixture()
def db(tmp_path):
    return DataDBManager(tmp_path / "data.db")


# ---------- 裁决 1：母库口径标签必须与实际取到的列一致 ----------
def _mother_db(tmp_path, *, qfq, adj, close, raw):
    sd = tmp_path / "sd2"
    (sd / "data" / "db").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sd / "data" / "db" / "stock_dashboard.db")
    conn.execute("""CREATE TABLE kline_daily (code TEXT, trade_date TEXT, open REAL,
                    high REAL, low REAL, close REAL, volume REAL, amount REAL,
                    close_adj REAL, close_raw REAL, close_qfq REAL)""")
    conn.execute("INSERT INTO kline_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("600519", "2026-10-23", close, close, close, close,
                  1000, 1e7, adj, raw, qfq))
    conn.commit()
    conn.close()
    return sd


@pytest.mark.parametrize("qfq,adj,expected", [
    (1495.0, 13000.0, "qfq"),      # 有前复权列 → 用 qfq
    (None, 13000.0, "hfq"),        # 只有后复权 → hfq
    (None, None, "qfq"),           # 两者皆空 → 兜底列 close；它实测就是前复权，**不能再报 raw**
])
def test_mother_adj_label_matches_actual_column(tmp_path, monkeypatch, qfq, adj, expected):
    from paper_trading.integration import mother as _m

    monkeypatch.delenv("STOCK_DASHBOARD_DIR", raising=False)
    sd = _mother_db(tmp_path, qfq=qfq, adj=adj, close=1495.0, raw=1495.0)
    _m.reset_cache()
    try:
        out = _m.daily_bars(["600519"], days=5, local_bars=None, mother=sd)
    finally:
        _m.reset_cache()
    blk = out["600519"]
    assert blk["available"] and blk["bars"][0]["close"] > 0
    assert blk["adj"] == expected


# ---------- 裁决 2-A：当日跨源对照（实时快照 vs 当日 raw 收盘）----------
def test_session_closed_gate():
    assert raw_feed.session_closed(CLOSED) is True
    assert raw_feed.session_closed(IN_SESSION) is False


def test_intraday_reference_keeps_positive_prices_only():
    got = raw_feed.intraday_reference(["000792", "600519"], quotes={
        "000792": {"price": 22.10, "date": "2026-10-28"},
        "600519": {"price": 0, "date": "2026-10-28"}})
    assert set(got) == {"000792"} and got["000792"]["price"] == 22.10


def test_check_raw_bars_accepts_matching_intraday_and_marks_checked():
    v = raw_feed.check_raw_bars([mk("000792", TODAY, 22.10)], {},
                                intraday={"price": 22.10, "date": "2026-10-28"},
                                day="2026-10-28", enforce_intraday=True)
    assert v["ok"] and v["intraday_checked"] == 1 and "2026-10-28" in v["verified_dates"]


def test_check_raw_bars_rejects_polluted_today_bar_via_intraday():
    # 事故复现：当日真实 22.10，喂进后复权 175.96
    v = raw_feed.check_raw_bars([mk("000792", TODAY, 175.96)], {},
                                intraday={"price": 22.10, "date": "2026-10-28"},
                                day="2026-10-28", enforce_intraday=True)
    assert not v["ok"] and v["mismatches"][0]["why"] == "intraday"
    assert v["mismatches"][0]["ratio"] == pytest.approx(7.9620, abs=1e-3)


def test_check_raw_bars_tolerates_missing_intraday_without_false_alarm():
    v = raw_feed.check_raw_bars([mk("000792", TODAY, 22.10)], {},
                                intraday=None, day="2026-10-28", enforce_intraday=True)
    assert v["ok"] and v["no_intraday"] == 1 and v["checked"] == 0


def test_intraday_not_applied_during_session():
    v = raw_feed.check_raw_bars([mk("000792", TODAY, 22.10)], {},
                                intraday={"price": 23.9, "date": "2026-10-28"},
                                day="2026-10-28", enforce_intraday=False)
    assert v["ok"] and v["intraday_checked"] == 0     # 盘中价与收盘价本就不同，不判


def test_sync_raw_rejects_symbol_whose_today_bar_disagrees_with_realtime(db):
    res = raw_feed.sync_raw(
        db, ["000792", "600519"], days=5,
        fetch=lambda s, d: [mk(s, TODAY, {"000792": 22.10, "600519": 999.0}[s])],
        reference=lambda syms, d: {s: {} for s in syms},
        today=TODAY, now=CLOSED,
        quotes={"000792": {"price": 22.10, "date": "2026-10-28"},
                "600519": {"price": 1330.75, "date": "2026-10-28"}})
    assert res["rejected"] == ["600519"]
    assert res["intraday_checked"] == 1 and res["intraday_enforced"] is True
    assert [b.close for b in db.get_raw_bars("000792")] == [22.10]
    assert db.get_raw_bars("600519") == []


def test_sync_raw_uses_mother_ref_for_history_and_intraday_for_today(db):
    bars = [mk("000792", date(2026, 10, 27), 22.21), mk("000792", TODAY, 22.10)]
    res = raw_feed.sync_raw(db, ["000792"], days=5,
                            fetch=lambda s, d: bars,
                            reference=lambda syms, d: {
                                "000792": {"2026-10-27": {"close_raw": 22.21}}},
                            today=TODAY, now=CLOSED,
                            quotes={"000792": {"price": 22.10, "date": "2026-10-28"}})
    assert res["written"] == 2 and not res["rejected"]
    # 两行都过了独立对照（历史靠母库、当日靠实时）⇒ 都能标 checked
    assert (db.raw_latest_checked("000792") or "").startswith("2026-10-28")


# ---------- 裁决 2-B：T+1 必须翻转，否则停线 ----------
def test_flip_watch_ok_when_checked_is_fresh(db):
    db.upsert_raw_bars([mk("000792", TODAY, 22.10)], checked_dates={"2026-10-28"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=TODAY)
    assert w["stop_line"] is False and w["violations"] == []


def test_flip_watch_flags_checked_not_flipped(db):
    # 10-26 的 bar 今天（10-28）才对照过 → 当日那根仍未翻转
    db.upsert_raw_bars([mk("000792", date(2026, 10, 26), 22.24),
                        mk("000792", TODAY, 22.10)],
                       checked_dates={"2026-10-26"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=TODAY)
    assert w["stop_line"] is True
    assert w["violations"] == ["000792"]
    assert w["per_symbol"]["000792"]["reason"] == "checked_not_flipped"


def test_flip_watch_flags_missing_raw_entirely(db):
    w = raw_feed.raw_flip_watch(db, ["000792"], today=TODAY)
    assert w["violations"] == ["000792"]
    assert w["per_symbol"]["000792"]["reason"] == "no_raw_data"


# ---------- 收盘判定必须按北京时间（生产机可能是 UTC）----------
def test_session_closed_uses_beijing_wall_clock_not_machine_tz():
    from datetime import timezone

    utc_07_20 = datetime(2026, 10, 29, 7, 20, tzinfo=timezone.utc)   # = 15:20 北京
    utc_06_30 = datetime(2026, 10, 29, 6, 30, tzinfo=timezone.utc)   # = 14:30 北京
    assert raw_feed.session_closed(utc_07_20) is True
    assert raw_feed.session_closed(utc_06_30) is False
    assert raw_feed.session_closed(datetime(2026, 10, 29, 14, 30)) is False  # naive=北京墙钟


# ---------- 未翻转的两种原因要分开（不能拿母库的锅停我们的线）----------
def _three_days(db, checked_dates):
    """10-26 / 10-27 / 10-28 三根，checked_dates 指定哪些已独立对照过。"""
    db.upsert_raw_bars([mk("000792", date(2026, 10, 26), 22.30),
                        mk("000792", date(2026, 10, 27), 22.21),
                        mk("000792", TODAY, 22.10)], checked_dates=set(checked_dates))


def test_flip_watch_allows_same_day_unverified(db):
    """当日（10-28）还没翻转，但昨日已对照过 ⇒ 在 T+1 宽限内，放行。"""
    _three_days(db, {"2026-10-27", "2026-10-26"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=TODAY,
                                ref_asof={"000792": "2026-10-28"})
    assert w["stop_line"] is False and w["violations"] == []


def test_flip_watch_waiting_reference_is_warning_not_stop(db):
    """已到 T+1（10-29）仍未翻转，但母库参照只到 10-27 ⇒ 等它，不停我们的线。"""
    _three_days(db, {"2026-10-27", "2026-10-26"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=date(2026, 10, 29),
                                ref_asof={"000792": "2026-10-27"})
    assert w["stop_line"] is False and w["warnings"] == ["000792"]
    assert w["per_symbol"]["000792"]["reason"] == "waiting_reference"


def test_flip_watch_stops_when_reference_exists_but_not_reverified(db):
    """已到 T+1、母库参照也覆盖到 10-28，我们却没重跑对照 ⇒ 停线（我们的锅）。"""
    _three_days(db, {"2026-10-27", "2026-10-26"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=date(2026, 10, 29),
                                ref_asof={"000792": "2026-10-28"})
    assert w["stop_line"] is True and w["violations"] == ["000792"]
    assert w["per_symbol"]["000792"]["reason"] == "checked_not_flipped"


def test_flip_watch_stops_when_feed_itself_is_stale(db):
    """连喂料都断了（最新 bar 落在两天前）⇒ 直接停线。"""
    db.upsert_raw_bars([mk("000792", date(2026, 10, 26), 22.30)],
                       checked_dates={"2026-10-26"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=date(2026, 10, 29))
    assert w["stop_line"] is True
    assert w["per_symbol"]["000792"]["reason"] == "raw_stale"


def test_mother_raw_close_asof_reports_reference_freshness(tmp_path, monkeypatch):
    from paper_trading.integration import mother as _m

    monkeypatch.delenv("STOCK_DASHBOARD_DIR", raising=False)
    sd = tmp_path / "sd3"
    (sd / "data" / "db").mkdir(parents=True)
    conn = sqlite3.connect(sd / "data" / "db" / "stock_dashboard.db")
    conn.execute("""CREATE TABLE kline_daily (code TEXT, trade_date TEXT, open REAL,
                    high REAL, low REAL, close REAL, volume REAL, amount REAL,
                    close_adj REAL, close_raw REAL, close_qfq REAL)""")
    conn.executemany("INSERT INTO kline_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        ("000792", "2026-10-27", 1, 1, 1, 1, 1, 1, 1, 22.21, 1),
        ("000792", "2026-10-28", 1, 1, 1, 1, 1, 1, 1, None, 1)])     # 当日 NULL
    conn.commit()
    conn.close()
    _m.reset_cache()
    try:
        assert _m.raw_close_asof(["000792"], mother=sd) == {"000792": "2026-10-27"}
    finally:
        _m.reset_cache()


def test_sync_raw_flags_when_today_bar_not_yet_published(db):
    """源还没出今日日K：跨源对照"没做成"必须显式计数，不能报成 0/0 像已校过。"""
    res = raw_feed.sync_raw(db, ["000792"], days=5,
                             fetch=lambda s, d: [mk(s, date(2026, 10, 27), 22.21)],
                             reference=lambda syms, d: {s: {} for s in syms},
                             today=TODAY, now=CLOSED,
                             quotes={"000792": {"price": 22.10, "date": "2026-10-28"}})
    assert res["intraday_enforced"] is True
    assert res["intraday_no_today_bar"] == 1
    assert res["intraday_checked"] == 0 and res["written"] == 1
