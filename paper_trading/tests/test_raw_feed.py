"""T31 原始价管道测试：口径标签、独立源对照、拒收、新鲜度、引擎闸。

全部离线：fetch/reference 注入假源，母库用临时 sqlite（只读连接）。
"""
from datetime import date, datetime
from pathlib import Path

import pytest

from paper_trading.data.db_manager import DataDBManager
from paper_trading.data import raw_feed
from paper_trading.data.source import BarSource
from paper_trading.models import Bar


def mkbar(sym, d, close, *, adjust="raw", amount=1e8, volume=1000,
          o=None, h=None, l=None):
    return Bar(symbol=sym, timestamp=datetime.combine(d, datetime.min.time()),
               open=o if o is not None else close, high=h if h is not None else close + 0.1,
               low=l if l is not None else close - 0.1, close=close,
               volume=volume, amount=amount, adjust=adjust)


@pytest.fixture()
def db(tmp_path):
    return DataDBManager(tmp_path / "data.db")


# ---- 口径标签（G-T13-1 的写入侧）----
def test_legacy_daily_bars_gets_qfq_label_without_rewriting(db):
    db.upsert_bars([mkbar("600519", date(2026, 10, 27), 1355.90, adjust=None)])
    with db._connect() as conn:
        row = conn.execute("SELECT adjust, close FROM daily_bars").fetchone()
    assert row["adjust"] == "qfq"          # 加列打标签，不改写任何既有数值
    assert float(row["close"]) == 1355.90


def test_upsert_raw_rejects_non_raw_caliber(db):
    with pytest.raises(ValueError, match="非 raw 口径"):
        db.upsert_raw_bars([mkbar("600519", date(2026, 10, 27), 166.54, adjust="qfq")])
    assert db.get_raw_bars("600519") == []


def test_raw_roundtrip_keeps_amount_and_caliber(db):
    db.upsert_raw_bars([mkbar("000792", date(2026, 10, 26), 22.24, amount=727573784.0)],
                       source="akshare:sina:raw", checked=True)
    bars = db.get_raw_bars("000792")
    assert len(bars) == 1 and bars[0].adjust == "raw"
    assert bars[0].amount == 727573784.0
    assert db.raw_latest("000792").startswith("2026-10-26")
    assert db.raw_latest_checked("000792").startswith("2026-10-26")


# ---- 独立源对照（母库 close_raw 当参照）----
def test_check_raw_matches_mother_and_marks_checked():
    bars = [mkbar("000792", date(2026, 10, 26), 22.24)]
    ref = {"2026-10-26": {"close_raw": 22.24}}
    v = raw_feed.check_raw_bars(bars, ref)
    assert v["ok"] and v["checked"] == 1 and v["no_reference"] == 0


def test_check_raw_catches_scale_pollution():
    # 事故本体：把后复权 175.96 当今日价（真实 22.24，6.4×）
    bars = [mkbar("000792", date(2026, 10, 26), 175.96)]
    v = raw_feed.check_raw_bars(bars, {"2026-10-26": {"close_raw": 22.24}})
    assert not v["ok"]
    assert v["mismatches"][0]["ratio"] == pytest.approx(7.9118, abs=1e-3)


def test_check_raw_rejects_absurd_price_even_without_reference():
    v = raw_feed.check_raw_bars([mkbar("600519", date(2026, 10, 26), 150_000.0)], {})
    assert not v["ok"] and v["absurd"][0]["why"] == "abs-price"


def test_sync_symbol_writes_only_when_clean(db):
    bars = [mkbar("000792", date(2026, 10, 26), 22.24)]
    bad = raw_feed.check_raw_bars([mkbar("000792", date(2026, 10, 26), 9.99)],
                                  {"2026-10-26": {"close_raw": 22.24}})
    r = raw_feed.sync_symbol("000792", bars, bad, db)
    assert r["status"] == "rejected" and r["written"] == 0
    assert db.get_raw_bars("000792") == []      # 有分歧整只拒收，不落半条


def test_sync_symbol_marks_checked_per_row_not_per_batch(db):
    """母库最新日 close_raw 实测为 NULL ⇒ 逐行标记，已验证的旧日不能被整批降级。"""
    bars = [mkbar("000792", date(2026, 10, 26), 22.24),
            mkbar("000792", date(2026, 10, 27), 22.21),
            mkbar("000792", date(2026, 10, 28), 22.10)]   # 参照缺该日
    v = raw_feed.check_raw_bars(bars, {"2026-10-26": {"close_raw": 22.24},
                                       "2026-10-27": {"close_raw": 22.21}})
    r = raw_feed.sync_symbol("000792", bars, v, db)
    assert r["status"] == "ok" and r["written"] == 3 and r["no_reference"] == 1
    assert db.raw_latest("000792").startswith("2026-10-28")
    assert db.raw_latest_checked("000792").startswith("2026-10-27")
    # 母库补上 10-28 后重跑同一批：该行自动翻成已验证（INSERT OR REPLACE）
    v2 = raw_feed.check_raw_bars(bars, {**{k: {"close_raw": c} for k, c in
                                           [("2026-10-26", 22.24), ("2026-10-27", 22.21),
                                            ("2026-10-28", 22.10)]}})
    raw_feed.sync_symbol("000792", bars, v2, db)
    assert db.raw_latest_checked("000792").startswith("2026-10-28")


# ---- 端到端（假源，不打网络）----
def test_sync_raw_end_to_end_with_injected_sources(db):
    days = [date(2026, 10, 26), date(2026, 10, 27)]
    fetched = {"000792": [mkbar("000792", d, c) for d, c in zip(days, (22.24, 22.21))],
               "600519": [mkbar("600519", days[0], 1349.98)]}
    refs = {"000792": {"2026-10-26": {"close_raw": 22.24}, "2026-10-27": {"close_raw": 22.21}},
            "600519": {"2026-10-26": {"close_raw": 9999.0}}}   # 故意不一致 → 拒收
    res = raw_feed.sync_raw(db, ["000792", "600519"], days=5,
                            fetch=lambda s, d: fetched[s],
                            reference=lambda s, d: refs)
    assert res["written"] == 2 and res["rejected"] == ["600519"]
    assert db.get_raw_bars("600519") == []


# ---- 新鲜度（G-T13-2 本地半边）----
def test_raw_freshness_flags_stale_and_missing(db):
    db.upsert_raw_bars([mkbar("000792", date(2026, 10, 23), 22.24)])
    db.upsert_raw_bars([mkbar("600036", date(2026, 10, 28), 11.11)])
    f = raw_feed.raw_freshness(db, ["000792", "600036", "601318"],
                               today=date(2026, 10, 28))
    assert f["000792"]["lag"] == 3 and f["000792"]["stale"] is True
    assert f["600036"]["lag"] == 0 and f["600036"]["stale"] is False
    assert f["601318"]["reason"] == "no_raw_data"


def test_factor_freshness_gate():
    ok = raw_feed.factor_freshness_ok(date(2026, 10, 28), date(2026, 10, 26))
    assert ok["ok"] is True and ok["lag_days"] == 2
    stale = raw_feed.factor_freshness_ok(date(2026, 10, 28), date(2026, 9, 30))
    assert stale["ok"] is False and stale["reason"] == "factor_stale"
    none = raw_feed.factor_freshness_ok(date(2026, 10, 28), None)
    assert none["ok"] is False and none["reason"] == "factor_unavailable"


# ---- 引擎取数口（fail-closed）----
def test_engine_raw_bars_fails_closed_without_data(db):
    src = BarSource(db, source="local")
    assert src.engine_raw_bars("000792", limit=5) == []
    assert src.last_engine_reject == "no_raw_data"


def test_engine_raw_bars_returns_fresh_raw(db):
    """T50-C 起：当日那根必须**有独立参照**（checked）才喂引擎，所以这里显式给 checked_dates。"""
    db.upsert_raw_bars([mkbar("000792", date(2026, 10, 28), 22.10)],
                       checked_dates={"2026-10-28"})
    src = BarSource(db, source="local")
    bars = src.engine_raw_bars("000792", limit=5, today=date(2026, 10, 28))
    assert len(bars) == 1 and bars[0].adjust == "raw"
    assert src.last_engine_reject is None


def test_engine_raw_bars_gate_can_be_disabled_for_troubleshooting(db):
    """`require_today_reference=False` 是显式排障口：放行，但默认值必须是开着的。"""
    db.upsert_raw_bars([mkbar("000792", date(2026, 10, 28), 22.10)])
    src = BarSource(db, source="local")
    bars = src.engine_raw_bars("000792", limit=5, today=date(2026, 10, 28),
                              require_today_reference=False)
    assert len(bars) == 1 and src.last_engine_reject is None


def test_engine_raw_bars_refuses_stale(db):
    db.upsert_raw_bars([mkbar("000792", date(2026, 10, 20), 22.0)])
    src = BarSource(db, source="local")
    assert src.engine_raw_bars("000792", limit=5, today=date(2026, 10, 28)) == []
    assert src.last_engine_reject.startswith("raw_stale")


# ---- 母库 close_raw 读取（只读连接）----
def test_mother_raw_close_map_reads_only_raw_columns(tmp_path):
    import sqlite3

    from paper_trading.integration import mother as _m

    sd = tmp_path / "sd"
    (sd / "data" / "db").mkdir(parents=True)
    conn = sqlite3.connect(sd / "data" / "db" / "stock_dashboard.db")
    conn.execute("""CREATE TABLE kline_daily (code TEXT, trade_date TEXT, open REAL,
                    high REAL, low REAL, close REAL, volume REAL, amount REAL,
                    close_adj REAL, close_qfq REAL, close_raw REAL)""")
    # 母库实测形态：OHLC/close 都是前复权，只有 close_raw 是不复权
    conn.execute("INSERT INTO kline_daily VALUES "
                 "('000792','2026-10-26',22.17,22.45,21.90,22.2403,32772237,727573784,"
                 "175.96,22.2403,22.24)")
    conn.commit()
    conn.close()
    _m.reset_cache()
    try:
        got = _m.raw_close_map(["000792"], days=5, mother=sd)
        assert got["000792"]["2026-10-26"]["close_raw"] == 22.24
        assert got["000792"]["2026-10-26"]["volume"] == 32772237
        assert _m.raw_close_map(["999999"], days=5, mother=sd)["999999"] == {}
    finally:
        _m.reset_cache()


# ---- 生产迁移面：老 data.db 首次打开必须原地升级，不改写既有数值 ----
def test_legacy_db_migration_keeps_rows_and_adds_raw_table(tmp_path):
    import sqlite3

    p = tmp_path / "data.db"
    conn = sqlite3.connect(p)
    conn.execute("""CREATE TABLE daily_bars (symbol TEXT NOT NULL, timestamp TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume INTEGER NOT NULL, turn REAL DEFAULT 0.0,
                    PRIMARY KEY (symbol, timestamp))""")
    conn.execute("INSERT INTO daily_bars VALUES "
                 "('000792','2026-10-26T00:00:00',22.17,22.45,21.90,22.2403,32772237,0.0062)")
    conn.execute("""CREATE TABLE minute_bars (symbol TEXT NOT NULL, ts TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL DEFAULT 0, amount REAL DEFAULT 0,
                    PRIMARY KEY (symbol, ts))""")
    conn.execute("INSERT INTO minute_bars VALUES ('000792','2026-10-28T15:00',22.05,22.10,22.05,22.10,1,1)")
    conn.commit()
    conn.close()

    d = DataDBManager(p)                              # 打开即迁移
    row = d.get_bars("000792")[0]
    assert row.close == 22.2403                       # 原 qfq 数值一字未动
    assert d.raw_latest("000792") is None             # 新表是空的，不会凭空造数
    m = d.get_minute_bars("000792")[0]
    assert m["adjust"] == "raw"                       # 分钟线补口径标签


def test_minute_upsert_labels_raw_caliber(db):
    db.upsert_minute_bars("000792", [{"ts": "2026-10-28T15:00", "open": 22.05,
                                      "high": 22.10, "low": 22.05, "close": 22.10,
                                      "volume": 100, "amount": 2210}])
    assert db.get_minute_bars("000792")[0]["adjust"] == "raw"
