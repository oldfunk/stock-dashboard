"""T95 · 面板 P0：收益曲线与今日卡的口径单测（2026-11-19）。"""
import json
import math
import sqlite3
import sys
import threading
from datetime import date, datetime, timedelta
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paper_trading.dashboard as d  # noqa: E402
from paper_trading.utils import trading_calendar as tc  # noqa: E402


def _mk_db(path, rows, *, initial=100000.0):
    """最小账本：account + nav_history（只测面板口径，不掺引擎）。"""
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL,"
              " initial_cash REAL, created_at TEXT)")
    c.execute("INSERT INTO account (cash, initial_cash) VALUES (?,?)", (0.0, initial))
    c.execute("CREATE TABLE nav_history (id INTEGER PRIMARY KEY, timestamp TEXT,"
              " cash REAL, market_value REAL, total_value REAL,"
              " available_cash REAL, pnl REAL, pnl_pct REAL)")
    for ts, tv, mv in rows:
        c.execute("INSERT INTO nav_history (timestamp, cash, market_value,"
                  " total_value, available_cash, pnl, pnl_pct)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (ts, 0.0, mv, tv, 0.0, tv - initial, tv / initial - 1))
    c.commit()
    c.close()


@pytest.fixture()
def cal(tmp_path, monkeypatch):
    p = tmp_path / "cal.json"
    tc.save_calendar(["2026-11-02", "2026-11-03", "2026-11-04",
                      "2026-11-05", "2026-11-06"], path=p)
    monkeypatch.setenv(tc.CALENDAR_ENV, str(p))
    tc.clear_calendar_cache()
    yield p
    tc.clear_calendar_cache()


def test_perf_metrics_known_series(tmp_path, cal):
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-02T20:50:00", 100000.0, 100000.0),
                ("2026-11-03T20:50:00", 101000.0, 101000.0),
                ("2026-11-04T20:50:00", 100000.0, 100000.0),
                ("2026-11-05T20:50:00", 99000.0, 99000.0)])
    out = d._perf_payload(str(db))
    s = out["summary"]
    assert out["initial_cash"] == 100000.0
    assert len(out["series"]) == 4
    assert s["total_return"] == pytest.approx(-0.01, abs=1e-6)
    assert s["total_pnl"] == pytest.approx(-1000.0, abs=1e-4)
    assert s["today_pnl"] == pytest.approx(-1000.0, abs=1e-4)
    assert s["today_pnl_pct"] == pytest.approx(-0.01, abs=1e-6)
    assert s["mdd"] == pytest.approx(1 - 99000 / 101000, abs=1e-6)
    assert s["mdd_date"] == "2026-11-05"
    assert s["current_drawdown"] == pytest.approx(1 - 99000 / 101000, abs=1e-6)
    assert s["exposure"] == pytest.approx(1.0, abs=1e-6)
    assert (s["win_days"], s["lose_days"]) == (1, 2)

    rets = [0.01, 100000 / 101000 - 1, -0.01]
    mean = sum(rets) / 3
    var = sum((r - mean) ** 2 for r in rets) / 2
    sd = math.sqrt(var)
    assert s["vol_annual"] == pytest.approx(sd * math.sqrt(252), abs=1e-6)
    assert s["sharpe"] == pytest.approx(mean / sd * math.sqrt(252), abs=1e-4)


def test_perf_same_day_duplicates_take_last(tmp_path, cal):
    """reset/补记会在同一天留下两行：曲线只认最后一条，不画出两个点。"""
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-03T10:00:00", 101000.0, 101000.0),
                ("2026-11-03T20:50:00", 100500.0, 100500.0),
                ("2026-11-04T20:50:00", 100000.0, 100000.0)])
    out = d._perf_payload(str(db))
    assert [x["date"] for x in out["series"]] == ["2026-11-03", "2026-11-04"]
    assert out["series"][0]["total_value"] == 100500.0


def test_perf_reports_missing_trading_days(tmp_path, cal):
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-02T20:50:00", 100000.0, 100000.0),
                ("2026-11-04T20:50:00", 100000.0, 100000.0)])
    cov = d._perf_payload(str(db))["coverage"]
    assert cov["expected"] == 3 and cov["present"] == 2
    assert cov["missing"] == ["2026-11-03"]


def test_perf_single_point_has_no_risk_stats(tmp_path, cal):
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-05T20:50:00", 97000.0, 97000.0)])
    s = d._perf_payload(str(db))["summary"]
    assert s["total_return"] == pytest.approx(-0.03)
    assert s["mdd"] is None and s["vol_annual"] is None
    assert s["win_days"] is None and s["lose_days"] is None


def test_perf_empty_db_is_not_an_error(tmp_path):
    db = tmp_path / "empty.db"
    db.write_bytes(b"")
    out = d._perf_payload(str(db))
    assert out["series"] == [] and out["summary"]["total_return"] is None
    assert out["coverage"]["present"] == 0 and out["asof"] is None


def test_today_checks_reads_last_record(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    today = date.today().isoformat()
    rec = {"kind": "daily_check", "at": today + "T15:30:03", "today": today,
           "green": 15, "warn": 3, "red": 0, "red_ids": [],
           "warn_ids": ["nav", "raw_gate"]}
    (logs / "daily_check.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    got = d._today_checks(str(tmp_path / "acct.db"), today)
    assert got["green"] == 15 and got["warn_ids"] == ["nav", "raw_gate"]
    assert d._today_checks(str(tmp_path / "acct.db"), "2020-01-01") is None


def _overview_db(path):
    today = date.today().isoformat()
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE fills (fill_id INTEGER PRIMARY KEY, symbol TEXT,"
              " direction INTEGER, volume INTEGER, price REAL, timestamp TEXT)")
    c.execute("INSERT INTO fills (symbol,direction,volume,price,timestamp)"
              " VALUES ('000001',1,100,10,?)", (today + "T09:32:00",))
    c.execute("INSERT INTO fills (symbol,direction,volume,price,timestamp)"
              " VALUES ('000002',-1,100,10,?)", (today + "T09:33:00",))
    c.execute("CREATE TABLE conditional_orders (id INTEGER PRIMARY KEY,"
              " created_at TEXT, filled_at TEXT, status TEXT)")
    for st, n, filled in (("armed", 2, ""), ("queued_rq", 1, ""),
                          ("filled", 1, today + "T09:47:05")):
        for i in range(n):
            c.execute("INSERT INTO conditional_orders (created_at, filled_at, status)"
                      " VALUES (?,?,?)", (today + "T09:00:00", filled, st))
    c.execute("CREATE TABLE rq_intents (biz_id TEXT PRIMARY KEY, created_at TEXT,"
              " status TEXT)")
    for st in ("executed", "rejected", "queued"):
        c.execute("INSERT INTO rq_intents (biz_id, created_at, status)"
                  " VALUES (?,?,?)", (st + "-1", today + "T09:30:00", st))
    c.execute("CREATE TABLE nav_history (id INTEGER PRIMARY KEY, timestamp TEXT,"
              " total_value REAL)")
    c.execute("INSERT INTO nav_history (timestamp,total_value) VALUES (?,?)",
              ((date.today() - timedelta(days=1)).isoformat() + "T20:50:00", 11000.0))
    c.execute("INSERT INTO nav_history (timestamp,total_value) VALUES (?,?)",
              (today + "T20:50:00", 12000.0))
    c.commit()
    c.close()


def _mk_data_db(path, raw_day=None, raw_px=None, minute_day=None, minute_px=None):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE daily_bars_raw (symbol TEXT, timestamp TEXT, close REAL)")
    if raw_day:
        c.execute("INSERT INTO daily_bars_raw VALUES ('000001',?,?)",
                  (raw_day + "T00:00:00", raw_px))
    c.execute("CREATE TABLE minute_bars (symbol TEXT, ts TEXT, close REAL, period TEXT)")
    if minute_day:
        c.execute("INSERT INTO minute_bars VALUES ('000001',?,?,'5')",
                  (minute_day + "T10:35", minute_px))
    c.commit()
    c.close()


def test_overview_counts_and_pnl(tmp_path, monkeypatch):
    db = tmp_path / "acct.db"
    _overview_db(db)
    from paper_trading.utils import heartbeat as hb
    monkeypatch.setattr(hb, "read_status", lambda **kw: {"healthy": True,
                                                        "alerts": [],
                                                        "checked_at": "x"})
    bridge = SimpleNamespace(
        get_status=lambda: {"total_value": 97000.0, "data_asof": "2026-11-19",
                            "positions": [], "cash": 0.0, "market_value": 97000.0},
        broker=SimpleNamespace(db_path=str(db)))
    out = d._overview_payload(bridge)
    assert out["fills"] == {"n": 2, "buy": 1, "sell": 1}
    assert out["conditions"]["armed"] == 2 and out["conditions"]["queued_rq"] == 1
    assert out["conditions"]["filled_today"] == 1
    assert out["intents"]["executed_today"] == 1
    assert out["intents"]["rejected_today"] == 1
    assert out["intents"]["pending"] == 1
    # 今天已有收盘 NAV ⇒ 用 last - prev，且不是估算
    assert out["today_pnl"] == pytest.approx(1000.0)
    assert out["estimated"] is False
    assert out["health"]["healthy"] is True
    assert out.get("quote_asof") is None      # 测试 bridge 没有 data_db


def test_overview_waits_for_today_price_instead_of_showing_zero(tmp_path, monkeypatch):
    """开盘前/数据未更新：今日盈亏必须是"—"（None），不许拿昨日价算成 0。

    2026-11-20 00:04 现网实测：旧逻辑返回 today_pnl=0.0 / estimated=true，
    页面显示"0.00 / 0.00% 盘中估算"——看起来像今天不涨不跌，实际是没数据。
    """
    db = tmp_path / "acct.db"
    _overview_db(db)
    c = sqlite3.connect(str(db))
    c.execute("DELETE FROM nav_history WHERE timestamp LIKE ?",
              (date.today().isoformat() + "%",))
    c.commit()
    c.close()
    from paper_trading.utils import heartbeat as hb
    monkeypatch.setattr(hb, "read_status", lambda **kw: {"healthy": True,
                                                        "alerts": []})
    bridge = SimpleNamespace(
        get_status=lambda: {"total_value": 97000.0, "data_asof": "x",
                            "positions": [{"symbol": "000001", "total_volume": 100,
                                           "current_price": 99.0}],
                            "cash": 1000.0, "market_value": 9900.0},
        broker=SimpleNamespace(db_path=str(db)))
    out = d._overview_payload(bridge)
    assert out["today_pnl"] is None and out["today_pnl_pct"] is None
    assert out["estimated"] is True and out["prices_ready"] is False


def test_overview_estimates_with_today_raw(tmp_path, monkeypatch):
    """有今日价（raw/分钟）才估算：live = 现金 + 今日价×股数，对昨日 NAV。"""
    db = tmp_path / "acct.db"
    _overview_db(db)
    c = sqlite3.connect(str(db))
    c.execute("DELETE FROM nav_history WHERE timestamp LIKE ?",
              (date.today().isoformat() + "%",))
    c.commit()
    c.close()
    data = tmp_path / "data.db"
    _mk_data_db(data, raw_day=date.today().isoformat(), raw_px=110.0)
    from paper_trading.utils import heartbeat as hb
    monkeypatch.setattr(hb, "read_status", lambda **kw: {"healthy": True,
                                                        "alerts": []})
    bridge = SimpleNamespace(
        get_status=lambda: {"total_value": 97000.0, "data_asof": "x",
                            "positions": [{"symbol": "000001", "total_volume": 100,
                                           "current_price": 99.0}],
                            "cash": 1000.0, "market_value": 9900.0},
        broker=SimpleNamespace(db_path=str(db)),
        data_db=SimpleNamespace(db_path=str(data)))
    out = d._overview_payload(bridge)
    assert out["prices_ready"] is True
    assert out["today_pnl"] == pytest.approx(1000.0)   # 1000 + 100*110 - 11000
    assert out["estimated"] is True
    assert out["quote_asof"] == date.today().isoformat()
    assert out["live_total"] == pytest.approx(12000.0)
    assert out["live_market_value"] == pytest.approx(11000.0)
    assert out["cash"] == pytest.approx(1000.0)


def test_limit_arg_clamps_bad_values():
    """坏 limit 不许 500，也不许 -1（SQLite 里 = 不限制）把整表拉出来。"""
    assert d._limit_arg({}) == 30
    assert d._limit_arg({"limit": ["abc"]}) == 30
    assert d._limit_arg({"limit": ["-1"]}) == 1
    assert d._limit_arg({"limit": ["999999"]}) == 500
    assert d._limit_arg({"limit": ["12"]}) == 12


def test_positions_use_raw_prices_same_as_nav(tmp_path):
    """现价/市值/走势必须走 raw（与 NAV 同源）：qfq 日线会晚一天，混用会
    让 KPI 与曲线差出几百块（11-19 实测 96,071 vs 95,800）。"""
    db = tmp_path / "data.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE daily_bars_raw (symbol TEXT, timestamp TEXT, close REAL)")
    c.executemany("INSERT INTO daily_bars_raw VALUES (?,?,?)",
                  [("000001", "2026-11-18T00:00:00", 100.0),
                   ("000001", "2026-11-19T00:00:00", 110.0)])
    c.commit()
    c.close()
    bridge = SimpleNamespace(
        get_status=lambda: {"positions": [{
            "symbol": "000001", "total_volume": 100, "available_volume": 100,
            "avg_cost": 90.0, "current_price": 99.0, "market_value": 9900.0}],
            "cash": 1000.0, "market_value": 9900.0, "total_value": 10900.0},
        broker=SimpleNamespace(get_all_positions=lambda: []),
        data_db=SimpleNamespace(db_path=str(db)))
    rows = d._positions_payload(bridge)
    assert rows[0]["current_price"] == 110.0
    assert rows[0]["market_value"] == 11000.0
    assert rows[0]["spark"] == [100.0, 110.0]
    assert len(rows[0]["spark"]) == 2
    assert rows[0]["price_source"] == "收盘"
    assert rows[0]["price_asof"] == "2026-11-19"


def test_positions_prefer_today_minute_then_today_raw(tmp_path):
    """盘中（raw 还没今天）用当日分钟线；收盘后 raw 今天优先于分钟。"""
    today = date.today().isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()
    bridge_kw = dict(
        get_status=lambda: {"positions": [{
            "symbol": "000001", "total_volume": 100, "available_volume": 100,
            "avg_cost": 90.0, "current_price": 99.0, "market_value": 9900.0}],
            "cash": 1000.0, "market_value": 9900.0, "total_value": 10900.0},
        broker=SimpleNamespace(get_all_positions=lambda: []))

    m = tmp_path / "minute.db"
    _mk_data_db(m, raw_day=yday, raw_px=100.0, minute_day=today, minute_px=108.0)
    rows = d._positions_payload(SimpleNamespace(
        data_db=SimpleNamespace(db_path=str(m)), **bridge_kw))
    assert rows[0]["current_price"] == 108.0
    assert rows[0]["price_source"] == "分钟"
    assert rows[0]["price_time"] == "10:35"
    assert rows[0]["market_value"] == 10800.0

    r = tmp_path / "raw_today.db"
    _mk_data_db(r, raw_day=today, raw_px=110.0, minute_day=today, minute_px=108.0)
    rows = d._positions_payload(SimpleNamespace(
        data_db=SimpleNamespace(db_path=str(r)), **bridge_kw))
    assert rows[0]["current_price"] == 110.0
    assert rows[0]["price_source"] == "收盘"
    assert rows[0]["price_asof"] == today


def test_intents_endpoint(tmp_path):
    db = tmp_path / "acct.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE rq_intents (biz_id TEXT PRIMARY KEY, symbol TEXT,"
              " side TEXT, volume INTEGER, rule_ref TEXT, status TEXT,"
              " created_at TEXT, result TEXT)")
    for i, (st, ref) in enumerate((("executed", "cond:7"), ("rejected", "engine"))):
        c.execute("INSERT INTO rq_intents VALUES (?,?,?,?,?,?,?,?)",
                  (f"i{i}", "000001", "buy", 100, ref, st,
                   f"2026-11-20T09:3{i}:00", '{"reason":"x"}'))
    c.commit()
    c.close()

    class H(d.Handler):
        account_db = str(db)
        data_db = str(tmp_path / "data.db")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{srv.server_port}/api/intents?limit=1",
                timeout=10) as r:
            one = json.loads(r.read().decode())
        with urllib.request.urlopen(
                f"http://127.0.0.1:{srv.server_port}/api/intents?limit=abc",
                timeout=10) as r:
            bad = json.loads(r.read().decode())
    finally:
        srv.shutdown()
        srv.server_close()
    assert one["ok"] and len(one["data"]) == 1
    assert bad["ok"] and len(bad["data"]) == 2


def test_fifo_realized_includes_fees_and_win_rate():
    fills = [
        {"symbol": "A", "direction": 1, "volume": 100, "price": 10.0,
         "commission": 5.0, "stamp_duty": 0.0, "transfer_fee": 0.0},
        {"symbol": "A", "direction": 1, "volume": 100, "price": 12.0,
         "commission": 5.0, "stamp_duty": 0.0, "transfer_fee": 0.0},
        {"symbol": "A", "direction": -1, "volume": 150, "price": 15.0,
         "commission": 5.0, "stamp_duty": 0.0, "transfer_fee": 0.0},
    ]
    out = d._fifo_realized(fills)
    assert out["realized_pnl"] == pytest.approx(637.5)
    assert out["fees_total"] == pytest.approx(15.0)
    assert (out["win_trades"], out["lose_trades"], out["closed_trades"]) == (1, 0, 1)
    assert out["win_rate"] == 1.0
    assert out["unmatched_sell_volume"] == {}


def test_fifo_unmatched_sells_are_not_counted_as_profit():
    """历史重建的卖出没有买入成本时，宁可报未匹配，不拿 0 成本虚增收益。"""
    out = d._fifo_realized([
        {"symbol": "A", "direction": -1, "volume": 100, "price": 10.0,
         "commission": 5.0, "stamp_duty": 0.0, "transfer_fee": 0.0},
    ])
    assert out["realized_pnl"] == 0.0
    assert out["unmatched_sell_volume"] == {"A": 100}
    assert out["closed_trades"] == 0


def test_perf_payload_exposes_realized_pnl(tmp_path, cal):
    db = tmp_path / "a.db"
    _mk_db(db, [("2026-11-19T20:50:00", 97000.0, 97000.0)])
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE fills (fill_id INTEGER PRIMARY KEY, symbol TEXT,"
              " direction INTEGER, volume INTEGER, price REAL, commission REAL,"
              " stamp_duty REAL, transfer_fee REAL, timestamp TEXT)")
    c.execute("INSERT INTO fills VALUES (1,'A',1,100,10,5,0,0,'2026-11-18')")
    c.execute("INSERT INTO fills VALUES (2,'A',-1,100,12,5,0,0,'2026-11-19')")
    c.commit()
    c.close()
    s = d._perf_payload(str(db))["summary"]
    assert s["realized_pnl"] == pytest.approx(190.0)   # 1200-5 - (1000+5)
    assert s["closed_trades"] == 1 and s["win_rate"] == 1.0


def test_export_csv_endpoints(tmp_path):
    db = tmp_path / "acct.db"
    _mk_db(db, [("2026-11-19T20:50:00", 97000.0, 97000.0)])
    import urllib.error
    import urllib.request

    class H(d.Handler):
        account_db = str(db)
        data_db = str(tmp_path / "data.db")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with urllib.request.urlopen(base + "/api/export.csv?type=nav", timeout=10) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
            disp = r.headers.get("Content-Disposition", "")
        assert ctype.startswith("text/csv") and "attachment" in disp
        assert raw.startswith(b"\xef\xbb\xbf")            # Excel 中文 BOM
        text = raw.decode("utf-8-sig")
        assert text.splitlines()[0].startswith("id,timestamp,cash")
        assert "2026-11-19T20:50:00" in text
        with urllib.request.urlopen(
                base + "/api/export.csv?type=performance", timeout=10) as r:
            perf = r.read().decode("utf-8-sig")
        assert perf.splitlines()[0] == "date,total_value,ret,drawdown"
        try:
            urllib.request.urlopen(base + "/api/export.csv?type=nope", timeout=10)
            raise AssertionError("未知类型应 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_performance_endpoint(tmp_path):
    db = tmp_path / "acct.db"
    _mk_db(db, [("2026-11-18T20:50:00", 96000.0, 96000.0),
                ("2026-11-19T20:50:00", 97000.0, 97000.0)])

    class H(d.Handler):
        account_db = str(db)
        data_db = str(tmp_path / "data.db")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import urllib.request

    def get_json(path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{srv.server_port}{path}", timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    try:
        _st, body = get_json("/api/performance")
        assert body["ok"] and body["data"]["summary"]["exposure"] == pytest.approx(1.0)
        assert len(body["data"]["series"]) == 2
        st, bad = get_json("/api/nav?limit=abc")
        assert st == 200 and len(bad["data"]) == 2      # 坏值回默认，不 500
        st, neg = get_json("/api/nav?limit=-1")
        assert st == 200 and len(neg["data"]) == 1      # 负数夹到 1，不整表返回
    finally:
        srv.shutdown()
        srv.server_close()
