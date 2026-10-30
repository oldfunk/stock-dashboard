"""价格历史全市场回填回归测试（2026-10-25）。

背景：审计实证 `kline_daily` 只有 49 只（2026-10-23 有价 24 只），回测
2016–2019 可选域为 0；旧脚本 target_codes() 只取最新 25 条筛选 + 观察池 + 钉选。
本脚本新增 `--all`（所有有年报的股票）+ 进度表断点续跑。
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "p.db"))
    db_mod.init_database()
    return db_mod


def _annual(code):
    db_mod.FinancialHistoryDAO().batch_save([{
        "stock_code": code, "report_date": "2025-12-31", "roe": 10.0,
    }])


def _row(day="2025-01-02"):
    return {"trade_date": day, "open": 1.0, "close": 1.0, "high": 1.0,
            "low": 1.0, "volume": 0, "amount": 0, "turnover": 0}


def test_fetch_kline_curl_parses_eastmoney_klines(monkeypatch):
    """curl 直连解析：日期/OHLC/量额；坏行跳过；secid 与 beg 正确。"""
    from scripts import backfill_price_history as s
    seen = {}

    def fake_json(url, timeout=15):
        seen["url"] = url
        return {"data": {"klines": ["2024-01-02,10,11,12,9,100,1000,0",
                                    "bad,line"]}}

    monkeypatch.setattr(s, "_curl_json", fake_json)
    rows = s.fetch_kline_curl("600519", "2016-01-01")
    assert rows == [{"trade_date": "2024-01-02", "open": 10.0, "close": 11.0,
                     "high": 12.0, "low": 9.0, "volume": 100.0,
                     "amount": 1000.0, "turnover": None}]
    assert "secid=1.600519" in seen["url"]
    assert "beg=20160101" in seen["url"]


def test_fetch_kline_curl_backs_off_then_succeeds(monkeypatch):
    """限流时指数退避重试，而不是直接放弃（2026-10-26 实测约 35 次后限流）。"""
    from scripts import backfill_price_history as s
    calls, sleeps = [], []

    def fake_json(url, timeout=15):
        calls.append(url)
        if len(calls) < 3:
            return None
        return {"data": {"klines": ["2024-01-02,1,2,3,0.5,10,20,0"]}}

    monkeypatch.setattr(s, "_curl_json", fake_json)
    monkeypatch.setattr(s.time, "sleep", lambda sec: sleeps.append(sec))
    rows = s.fetch_kline_curl("600519", "2016-01-01")
    assert len(rows) == 1
    assert calls == [calls[0]] * 3
    assert sleeps == [15, 30]


def test_fetch_kline_tencent_qfq_paginates(monkeypatch):
    """腾讯 fqkline：每页 640 天，按最早日期向前翻页，合并去重。"""
    from scripts import backfill_price_history as s
    calls = []
    pages = [
        {"data": {"sh600519": {"qfqday": [
            ["2024-03-04", "1", "2", "3", "0.5", "10"],
            ["2024-03-05", "1", "2", "3", "0.5", "10"],
        ]}}},
        {"data": {"sh600519": {"qfqday": [
            ["2023-12-01", "1", "2", "3", "0.5", "10"],
        ]}}},
    ]

    def fake(url, timeout=15):
        calls.append(url)
        return pages.pop(0)

    monkeypatch.setattr(s, "_tx_get_json", fake)
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)
    rows = s.fetch_kline_tencent_qfq("600519", "2023-01-01", page=2)
    assert [r["trade_date"] for r in rows] == ["2023-12-01", "2024-03-04",
                                               "2024-03-05"]
    assert len(calls) == 2
    assert ",2024-03-03," in calls[1]


def test_merge_price_model_builds_three_columns():
    """raw + 后复权 → raw / adj / 展示 close（正数前复权，锚定最新价）。"""
    from scripts.backfill_price_history import merge_price_model
    raw = [
        {"trade_date": "2020-01-02", "open": 10, "close": 10, "high": 11,
         "low": 9, "volume": 1, "amount": 2, "turnover": 0.1, "shares": 100},
        {"trade_date": "2020-01-03", "open": 11, "close": 11, "high": 12,
         "low": 10, "volume": 1, "amount": 2, "turnover": 0.1, "shares": 100},
    ]
    adj = [
        {"trade_date": "2020-01-02", "open": 100, "close": 100, "high": 110,
         "low": 90},
        {"trade_date": "2020-01-03", "open": 110, "close": 110, "high": 120,
         "low": 100},
    ]
    rows = merge_price_model(raw, adj)
    assert [r["close"] for r in rows] == [10.0, 11.0]
    assert [r["close_raw"] for r in rows] == [10.0, 11.0]
    assert [r["close_adj"] for r in rows] == [100.0, 110.0]
    assert rows[0]["shares"] == 100


def test_all_mode_covers_fundamental_universe_and_resumes(db, monkeypatch, capsys):
    from scripts import backfill_price_history as s
    _annual("600519")
    _annual("000001")
    called = []

    def fake(code, start_date=None, adjust="qfq"):
        called.append(code)
        return [_row()]

    monkeypatch.setattr(s, "fetch_kline_sina_adjusted", fake)
    # 600519 已完成：--all 不得重复抓取
    db_mod.FinancialHistoryDAO().mark_backfill_done(s.PROGRESS_TASK, "600519", 1, "done")
    monkeypatch.setattr("sys.argv", ["backfill", "--all", "--sleep", "0"])
    assert s.main() == 0
    assert called == ["000001"]
    assert "回填 1/1 只" in capsys.readouterr().out
    with db_mod.db_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM kline_daily WHERE code='000001'").fetchone()[0]
    assert n == 1


def test_no_data_is_skipped_unless_retry(db, monkeypatch, capsys):
    from scripts import backfill_price_history as s
    _annual("600519")
    calls = []

    def empty(code, start_date=None, adjust="qfq"):
        calls.append(code)
        return []

    monkeypatch.setattr(s, "fetch_kline_sina_adjusted", empty)
    # 批12-b：主源空会走备源复核（akshare）——备源同样打桩，
    # 否则测试会真打网络/真出数，no_data 断言失去意义
    monkeypatch.setattr(s, "fetch_via_akshare", lambda code, start_date=None: [])
    monkeypatch.setattr("sys.argv", ["backfill", "--all", "--sleep", "0"])
    assert s.main() == 0
    assert calls == ["600519"]
    assert "回填 0/1 只" in capsys.readouterr().out

    # 默认跳过 no_data
    assert s.main() == 0
    assert calls == ["600519"]
    assert "无待回填代码" in capsys.readouterr().out

    # --retry-no-data 才重试
    monkeypatch.setattr("sys.argv", ["backfill", "--all", "--sleep", "0",
                                     "--retry-no-data"])
    assert s.main() == 0
    assert calls == ["600519", "600519"]


def test_consecutive_failures_abort_and_mark_partial(db, monkeypatch, capsys):
    """上游限流时连续失败即中止，标 partial（可重试），不把全市场标成 no_data。"""
    from scripts import backfill_price_history as s
    for code in ("600519", "000001", "000002", "000003", "000004", "000005"):
        _annual(code)
    calls = []

    def boom(code, start_date=None):
        calls.append(code)
        raise RuntimeError("connection reset")

    monkeypatch.setattr(s, "fetch_kline_sina_adjusted", boom)
    # 批12-b：主源异常也会先走备源复核——备源同步打桩为异常，
    # 保证"连续失败中止"的语义仍由主源失败计数驱动
    monkeypatch.setattr(s, "fetch_via_akshare", boom)
    monkeypatch.setattr("sys.argv", ["backfill", "--all", "--sleep", "0"])
    assert s.main() == 0
    out = capsys.readouterr().out
    assert "连续失败已中止" in out
    assert len(calls) == 5          # 第 6 只不再打上游
    with db_mod.db_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM backfill_progress"
                         " WHERE task='price_history' AND status='partial'").fetchone()[0]
    assert n == 5
