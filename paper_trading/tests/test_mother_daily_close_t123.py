"""T123：母库 daily_close 作为 raw 收盘参照的补洞（C3）。"""
import sqlite3

from paper_trading.integration import mother as m


def _mother(tmp_path, *, with_kline=True, with_daily=True):
    sd = tmp_path / "sd"
    (sd / "data" / "db").mkdir(parents=True)
    p = sd / "data" / "db" / "stock_dashboard.db"
    c = sqlite3.connect(p)
    if with_kline:
        c.execute("CREATE TABLE kline_daily (code TEXT, trade_date TEXT,"
                  " close_raw REAL, volume REAL, amount REAL)")
        # 11-25/26 有 raw，11-27 缺（模拟母库按标的回填滞后）
        c.executemany("INSERT INTO kline_daily VALUES (?,?,?,?,?)", [
            ("000858", "2026-11-25", 20.0, 1000, 20000),
            ("000858", "2026-11-26", 20.5, 1100, 22550),
        ])
    if with_daily:
        c.execute("CREATE TABLE daily_close (code TEXT, trade_date TEXT,"
                  " close REAL, amount REAL, volume REAL)")
        c.executemany("INSERT INTO daily_close VALUES (?,?,?,?,?)", [
            ("000858", "2026-11-26", 20.5, 22550, 1100),
            ("000858", "2026-11-27", 21.0, 23100, 1100),
        ])
    c.commit()
    c.close()
    m.reset_cache()
    return sd


def test_raw_close_map_fills_gap_from_daily_close(tmp_path):
    sd = _mother(tmp_path)
    try:
        got = m.raw_close_map(["000858"], days=10, mother=sd)
        rows = got["000858"]
        assert rows["2026-11-25"]["close_raw"] == 20.0
        assert rows["2026-11-25"]["ref_source"] == "kline_daily"
        # 11-26 两表都有，保留 kline（权威 raw 列）
        assert rows["2026-11-26"]["ref_source"] == "kline_daily"
        # 11-27 只有 daily_close，补洞
        assert rows["2026-11-27"]["close_raw"] == 21.0
        assert rows["2026-11-27"]["ref_source"] == "daily_close"
    finally:
        m.reset_cache()


def test_raw_close_asof_uses_daily_close_when_kline_lags(tmp_path):
    sd = _mother(tmp_path)
    try:
        assert m.raw_close_asof(["000858"], mother=sd)["000858"] == "2026-11-27"
    finally:
        m.reset_cache()


def test_daily_close_only_mother_still_works(tmp_path):
    sd = _mother(tmp_path, with_kline=False)
    try:
        got = m.raw_close_map(["000858"], days=10, mother=sd)["000858"]
        assert got["2026-11-27"]["close_raw"] == 21.0
        assert m.raw_close_asof(["000858"], mother=sd)["000858"] == "2026-11-27"
    finally:
        m.reset_cache()
