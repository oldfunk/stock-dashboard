"""批12 回归测试：北交所三分法接线（12-a）+ no_data 判败加固（12-b）。

三条主线：
1. **三分法**：`market_of` 及其派生格式（新浪/腾讯 symbol、东财 secid、F10 前缀、
   datacenter SECUCODE）对沪深保持逐字不变，对 920/83/87/43 出 BJ 形式；
2. **能力边界不许伪装成"没数据"**：腾讯 K 线实测供不了北交所 -> 抛
   `SourceUnsupportedError`，回填侧既不写库也不记 `no_data`/`partial`；
3. **备源复核**：主源空/异常时先让列模型兼容的备源复核一次，
   任一源出数记 done、两源皆空才记 no_data、主源是失败则记 partial。
"""

from types import SimpleNamespace

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "bj.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _row(day="2026-10-29", close=10.0):
    return {"trade_date": day, "open": close, "close": close, "high": close,
            "low": close, "volume": 1000.0, "amount": 1e5, "turnover": 0.5,
            "close_raw": close, "close_adj": close * 2}


def _annual(code, date="2025-12-31"):
    db_mod.FinancialHistoryDAO().batch_save(
        [{"stock_code": code, "report_date": date, "roe": 10.0}])


# ── 1. 三分法与派生格式 ────────────────────────────────────────────────────

@pytest.mark.parametrize("code,market", [
    ("600519", "SH"), ("688981", "SH"), ("900901", "SH"),      # 沪A/科创/沪B
    ("000001", "SZ"), ("301139", "SZ"), ("200011", "SZ"),       # 深A/创B/深B
    ("920000", "BJ"), ("830799", "BJ"), ("873122", "BJ"), ("430139", "BJ"),
])
def test_market_of_three_way(code, market):
    from src.utils import market_of
    assert market_of(code) == market


def test_market_of_survives_whitespace_and_empty():
    from src.utils import market_of
    assert market_of("  920000 ") == "BJ"
    assert market_of("") == "SZ"            # 未知号段保持旧默认（深市），不抛异常
    assert market_of(None) == "SZ"


def test_derived_formats_per_market():
    from src.utils import em_f10_symbol, em_secid, em_secucode, sina_symbol, tc_encode
    assert sina_symbol("600519") == "sh600519"
    assert sina_symbol("920000") == "bj920000"
    assert tc_encode("920000") == "bj920000"          # 腾讯实测认 bj 前缀（86 字段）
    assert tc_encode("900901") == "sh900901"         # 沪 B 不被北交所规则抢走
    assert em_secid("600519") == "1.600519"
    assert em_secid("000001") == "0.000001"
    assert em_secid("920000") == "0.920000", "北交所 secid 在 0. 侧（实测 0.920000 有日线）"
    assert em_f10_symbol("920000") == "BJ920000"
    assert em_secucode("920000") == "920000.BJ"


def test_collector_helpers_delegate():
    from src.collector import balance_sheet_fetcher as b
    from src.collector import mainfin_fetcher as m
    from src.collector.akshare_fetcher import _code_to_em
    assert b._em_symbol("920000") == "BJ920000"
    assert b._em_symbol("600519") == "SH600519"
    assert m._secucode("920000") == "920000.BJ"
    assert _code_to_em("920000") == "920000.BJ"
    assert _code_to_em("000001") == "000001.SZ"


def test_kline_em_passes_bare_code_to_akshare(monkeypatch):
    """`stock_zh_a_hist` 的 symbol 是裸代码——它自己拼 secid（6→1，其余→0）。"""
    from src.collector import akshare_fetcher as ak_f
    seen = {}

    class FakeDF:
        empty = True

    def fake_hist(**kwargs):
        seen.update(kwargs)
        return FakeDF()

    monkeypatch.setattr(ak_f, "ak", SimpleNamespace(stock_zh_a_hist=fake_hist))
    assert ak_f._fetch_kline_em("920000", "20240101", "20261030", "") == []
    assert seen["symbol"] == "920000", "不能再传 920000.BJ（akshare 会拼成 0.920000.BJ）"


# ── 2. 能力边界：腾讯供不了 BJ ──────────────────────────────────────────────

def test_fetch_kline_tx_raises_for_bj(monkeypatch):
    from src.collector.akshare_fetcher import SourceUnsupportedError, _fetch_kline_tx

    with pytest.raises(SourceUnsupportedError) as e:
        _fetch_kline_tx("920000", "20240101", "20261030")
    assert "北交所" in str(e.value)


def test_fetch_kline_tx_still_works_for_sz(monkeypatch):
    from src.collector import akshare_fetcher as ak_f

    class FakeDF:
        empty = True

    seen = {}

    def fake_tx(**kwargs):
        seen.update(kwargs)
        return FakeDF()

    monkeypatch.setattr(ak_f, "ak", SimpleNamespace(stock_zh_a_hist_tx=fake_tx))
    assert ak_f._fetch_kline_tx("000792", "20240101", "20261030") == []
    assert seen["symbol"] == "sz000792"


def test_fetch_kline_data_propagates_capability_boundary(monkeypatch):
    """EM 空 + 腾讯供不了 BJ -> 抛出能力边界，而不是返回 []（=[] 会被记 no_data）。"""
    from src.collector import akshare_fetcher as ak_f
    from src.collector.akshare_fetcher import SourceUnsupportedError

    monkeypatch.setattr(ak_f, "_fetch_kline_em", lambda *a, **k: [])
    with pytest.raises(SourceUnsupportedError):
        ak_f.fetch_kline_data("920000", start_date="2024-01-01", adjust="qfq")


def _run_main(monkeypatch, argv):
    import scripts.backfill_price_history as s
    monkeypatch.setattr("sys.argv", ["backfill"] + argv)
    return s.main()
