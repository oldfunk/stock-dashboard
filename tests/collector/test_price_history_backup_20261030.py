"""批12-b 回归测试：回填侧备源复核与四态定性。

主源空/异常 -> 备源复核 -> done（任一源出数）/ partial（主源是失败）/ no_data（两源皆空）/ 跳过（结构性不支持）。
北交所三分法本身的用例在 test_bj_market_support_20261030.py。
"""

import pytest

from src.models import database as db_mod


def _row(day="2026-10-29", close=10.0):
    return {"trade_date": day, "open": close, "close": close, "high": close,
            "low": close, "volume": 1000.0, "amount": 1e5, "turnover": 0.5,
            "close_raw": close, "close_adj": close * 2}


def _annual(code, date="2025-12-31"):
    db_mod.FinancialHistoryDAO().batch_save(
        [{"stock_code": code, "report_date": date, "roe": 10.0}])


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "bjb.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _run_main(monkeypatch, argv):
    import scripts.backfill_price_history as s
    monkeypatch.setattr("sys.argv", ["backfill"] + argv)
    return s.main()
def test_backup_pairs_are_defined_and_distinct():
    from scripts.backfill_price_history import BACKUP_SOURCE, SOURCE_FETCHERS
    assert set(BACKUP_SOURCE) == set(SOURCE_FETCHERS), "每个源都得有备源"
    for src, backup in BACKUP_SOURCE.items():
        assert backup != src
    assert BACKUP_SOURCE["sina"] == "akshare"     # 同为三列模型，互换不掉契约
    assert BACKUP_SOURCE["tencent"] == "sina"     # 不选 curl：同限流域一起挂


def test_recheck_returns_backup_rows(monkeypatch):
    import scripts.backfill_price_history as s
    monkeypatch.setattr(s, "fetch_kline_data",
                        lambda code, start_date=None, adjust="qfq": [_row()])
    rows, used, notes = s.recheck_with_backup("600519", "2016-01-01", "sina")
    assert used == "akshare" and len(rows) == 1
    assert any("备源有数" in n for n in notes)


def test_recheck_both_empty(monkeypatch):
    import scripts.backfill_price_history as s
    monkeypatch.setattr(s, "fetch_kline_data",
                        lambda code, start_date=None, adjust="qfq": [])
    rows, used, notes = s.recheck_with_backup("600519", "2016-01-01", "sina")
    assert rows == [] and used is None
    assert any("备源同样无数据" in n for n in notes)


def test_recheck_backup_error_is_reported_not_silenced(monkeypatch):
    import scripts.backfill_price_history as s

    def boom(code, start_date=None, adjust="qfq"):
        raise RuntimeError("限流")

    monkeypatch.setattr(s, "fetch_kline_data", boom)
    rows, used, notes = s.recheck_with_backup("600519", "2016-01-01", "sina")
    assert rows == [] and used is None
    assert any("备源异常" in n and "RuntimeError" in n for n in notes)


def test_recheck_capability_boundary_on_backup(monkeypatch):
    import scripts.backfill_price_history as s
    from src.collector.akshare_fetcher import SourceUnsupportedError

    def unsupported(code, start_date=None, adjust="qfq"):
        raise SourceUnsupportedError("腾讯不支持北交所")

    monkeypatch.setattr(s, "fetch_kline_data", unsupported)
    rows, used, notes = s.recheck_with_backup("920000", "2016-01-01", "sina")
    assert rows == [] and any("结构上不支持" in n for n in notes)


# ── 4. 端到端定性（done / partial / no_data / skipped） ────────────────────

def test_backup_rescue_marks_done_and_writes_rows(db, monkeypatch, capsys):
    import scripts.backfill_price_history as s
    _annual("600519")
    monkeypatch.setattr(s, "fetch_kline_sina_adjusted", lambda c, sd=None: [])
    monkeypatch.setattr(s, "fetch_kline_data",
                        lambda c, start_date=None, adjust="qfq": [_row()])
    assert _run_main(monkeypatch, ["--all", "--sleep", "0"]) == 0
    out = capsys.readouterr().out
    assert "备源救回=1" in out and "回填 1/1 只" in out
    with db_mod.db_conn() as conn:
        status = conn.execute(
            "SELECT status, updated_rows FROM backfill_progress"
            " WHERE task='price_history' AND code='600519'").fetchone()
        assert status["status"] == "done" and status["updated_rows"] == 1
        assert conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0] == 1


def test_primary_failure_plus_empty_backup_is_partial_not_no_data(db, monkeypatch,
                                                                  capsys):
    """301139 一案：主源一次限流异常绝不能被记成 no_data（那会被断点续跑永久排除）。"""
    import scripts.backfill_price_history as s
    _annual("301139")

    def boom(code, start_date=None):
        raise RuntimeError("东财无响应")

    monkeypatch.setattr(s, "fetch_kline_sina_adjusted", boom)
    monkeypatch.setattr(s, "fetch_kline_data",
                        lambda c, start_date=None, adjust="qfq": [])
    assert _run_main(monkeypatch, ["--all", "--sleep", "0"]) == 0
    assert "no_data=0 partial=1" in capsys.readouterr().out
    with db_mod.db_conn() as conn:
        status = conn.execute(
            "SELECT status FROM backfill_progress"
            " WHERE task='price_history' AND code='301139'").fetchone()["status"]
    assert status == "partial"          # 可重试，不是 no_data


def test_both_sources_empty_is_no_data(db, monkeypatch, capsys):
    import scripts.backfill_price_history as s
    _annual("000004")
    monkeypatch.setattr(s, "fetch_kline_sina_adjusted", lambda c, sd=None: [])
    monkeypatch.setattr(s, "fetch_kline_data",
                        lambda c, start_date=None, adjust="qfq": [])
    assert _run_main(monkeypatch, ["--all", "--sleep", "0"]) == 0
    assert "no_data=1" in capsys.readouterr().out
    with db_mod.db_conn() as conn:
        assert conn.execute("SELECT status FROM backfill_progress"
                            " WHERE code='000004'").fetchone()["status"] == "no_data"


def test_bj_code_on_tencent_source_is_skipped_without_progress(db, monkeypatch,
                                                               capsys):
    """源不支持 BJ -> 既不写库也不记进度，换 sina/curl 重跑仍会捞到它。"""
    import scripts.backfill_price_history as s
    _annual("920000")
    assert _run_main(monkeypatch, ["--all", "--source", "tencent", "--sleep", "0"]) == 0
    out = capsys.readouterr().out
    assert "结构性跳过=1" in out and "tencent_unsupported_bj" in out
    with db_mod.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM backfill_progress").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM financial_history"
                            " WHERE stock_code='920000'").fetchone()[0] == 1


def test_sina_source_maps_bj_symbol_for_curl_path(db, monkeypatch):
    """curl 源的 secid 必须是 0.920000（沪侧 1. 会取不到）。"""
    import scripts.backfill_price_history as s
    _annual("920000")
    seen = {}

    def fake_curl_json(url, **kw):
        seen["url"] = url
        return {"data": {"klines": []}}

    monkeypatch.setattr(s, "_curl_json", fake_curl_json)
    assert s.fetch_kline_curl("920000", "2016-01-01") == []
    assert "secid=0.920000" in seen["url"]
def test_script_tencent_source_raises_for_bj():
    from scripts.backfill_price_history import fetch_kline_tencent_qfq
    from src.collector.akshare_fetcher import SourceUnsupportedError
    with pytest.raises(SourceUnsupportedError):
        fetch_kline_tencent_qfq("920000", "2016-01-01")


def test_script_symbols_use_shared_rule():
    from scripts.backfill_price_history import _sina_symbol, _tx_symbol
    assert _sina_symbol("920000") == "bj920000"
    assert _tx_symbol("920000") == "bj920000"
    assert _sina_symbol("600519") == "sh600519"


# ── 3. （备源复核的用例见 test_price_history_backup_20261030.py）
# ── 3. 备源复核 ────────────────────────────────────────────────────────────
