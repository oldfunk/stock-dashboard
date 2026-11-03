"""批13-a 深度回填回归测试（2026-10-31，全 mock，不打网络）。

三条主线：
1. **只补早段、绝不重写已有行**（含 overlap 那天）；
2. **overlap 日当锚点桥**：早段的展示价（qfq 列）按现网接缝比值缩放，
   接缝连续；`close_adj`（后复权）与锚点无关，原样写并当作一致性检查；
3. 四态与续传语义：no_data（上市更晚）/ partial（异常，可重试）/ done，
   `--dry-run` 零写入，进度表按 task 隔离。
"""

import json

import pytest

from src.models import database as db_mod

FIRST = "2016-01-04"
EARLY = "2005-01-04"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "depth.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _row(day, close, adj, raw=None):
    return {"trade_date": day, "open": close, "close": close, "high": close,
            "low": close, "volume": 1000.0, "amount": 1e6, "turnover": 0.5,
            "close_raw": raw if raw is not None else close, "close_adj": adj}


def _seed(code="600519", first=FIRST):
    """现网只有 first 及之后的行：close=100、close_adj=200（接缝比值 0.5）。"""
    db_mod.KlineDAO().upsert_many(code, [
        _row(first, 100.0, 200.0),
        _row("2016-01-05", 101.0, 202.0),
    ])


def _get(code, day):
    with db_mod.db_conn() as conn:
        r = conn.execute("SELECT * FROM kline_daily WHERE code = ?"
                         " AND trade_date = ?", (code, day)).fetchone()
    return dict(r) if r else None


def _patch_fetch(monkeypatch, depth, by_code):
    def fake(code, start_date, retries=3, end_date=None):
        rows = by_code.get(code)
        if isinstance(rows, Exception):
            raise rows
        return [dict(r) for r in (rows or [])]
    monkeypatch.setattr(depth, "fetch_kline_sina_adjusted", fake)


# ── 1. 目标集与续传 ────────────────────────────────────────────────────────

def test_oldest_dates_only_for_codes_with_kline(db):
    from scripts.backfill_price_depth import oldest_dates
    _seed("600519")
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO stock_snapshot (code, name, market, snapshot_date)"
                     " VALUES ('000001', '无行情票', 'A', '2026-10-30')")
    assert oldest_dates() == [("600519", FIRST)]


def test_pending_targets_excludes_already_deep_and_honours_flags(db):
    from scripts.backfill_price_depth import pending_targets
    _seed("600519", first=FIRST)
    _seed("000001", first="2004-01-02")          # 已经够深
    targets = [("600519", FIRST), ("000001", "2004-01-02")]
    assert pending_targets(targets, "2005-01-01") == [("600519", FIRST)]
    # 进度表：done/no_data 默认排除；partial 默认重试
    fh = db_mod.FinancialHistoryDAO()
    fh.mark_backfill_done("price_depth", "600519", 10, status="done",
                          covered_report_date=FIRST)
    assert pending_targets(targets, "2005-01-01") == []
    fh.mark_backfill_done("price_depth", "600519", 0, status="no_data")
    assert pending_targets(targets, "2005-01-01") == []
    assert pending_targets(targets, "2005-01-01",
                           retry_no_data=True) == [("600519", FIRST)]
    # partial 默认就重试：数小时的夜跑撞上游抽风是常态，不该靠人记得加开关
    fh.mark_backfill_done("price_depth", "600519", 0, status="partial")
    assert pending_targets(targets, "2005-01-01") == [("600519", FIRST)]
    # 显式 --codes 必跑（不查进度、也不要求"还不够深"）
    assert pending_targets(targets, "2005-01-01",
                           codes=["000001"]) == [("000001", "2004-01-02")]


def test_limit_and_offset_page_through_targets(db):
    from scripts.backfill_price_depth import pending_targets
    targets = [(f"60000{i}", "2016-01-04") for i in range(5)]
    assert [c for c, _ in pending_targets(targets, "2005-01-01", limit=2)] == \
        ["600000", "600001"]
    assert [c for c, _ in pending_targets(targets, "2005-01-01", limit=2,
                                         offset=2)] == ["600002", "600003"]


# ── 2. 只写早段 + 锚点桥 ───────────────────────────────────────────────────

def test_only_earlier_rows_are_written_overlap_day_untouched(db, monkeypatch):
    import scripts.backfill_price_depth as depth
    _seed("600519")
    before = _get("600519", FIRST)
    _patch_fetch(monkeypatch, depth, {
        "600519": [_row(EARLY, 50.0, 80.0), _row(FIRST, 125.0, 200.0)]})
    res = depth.backfill_one("600519", FIRST, "2005-01-01")
    assert res["status"] == "done" and res["added"] == 1
    assert _get("600519", FIRST) == before, "overlap 那天被重写 = 锚点被改 = 留台阶"
    early = _get("600519", EARLY)
    assert early is not None


def test_early_rows_rescaled_to_seam_anchor_but_hfq_untouched(db, monkeypatch):
    """展示价按现网接缝比值 0.5 缩放；close_raw/close_adj 与锚点无关，原样写。"""
    import scripts.backfill_price_depth as depth
    _seed("600519")
    _patch_fetch(monkeypatch, depth, {
        "600519": [_row(EARLY, 50.0, 80.0, raw=40.0),
                   _row(FIRST, 125.0, 200.0, raw=100.0)]})
    res = depth.backfill_one("600519", FIRST, "2005-01-01")
    early = _get("600519", EARLY)
    assert early["close"] == 25.0                 # 50 × (库里 100/200)
    assert early["close_adj"] == 80.0             # 后复权，锚点无关
    assert early["close_raw"] == 40.0            # 不复权，锚点无关
    assert res["seam_pct"] == 0.0                 # 接缝 hfq 对得上


def test_seam_mismatch_is_counted_not_silently_written(db, monkeypatch):
    import scripts.backfill_price_depth as depth
    _seed("600519")
    _patch_fetch(monkeypatch, depth, {
        "600519": [_row(EARLY, 50.0, 80.0), _row(FIRST, 125.0, 100.0)]})
    res = depth.backfill_one("600519", FIRST, "2005-01-01")
    assert res["seam_pct"] == 50.0               # 200 vs 100 -> 偏差 50%


def test_missing_seam_bridge_falls_back_to_window_anchor(db, monkeypatch):
    """库里接缝日没有 close_adj（老行）-> 不猜锚点，按抓取窗口原样写并计入 no_seam_bridge。"""
    import scripts.backfill_price_depth as depth
    with db_mod.db_conn() as conn:
        conn.execute("UPDATE kline_daily SET close_adj = NULL WHERE code = '600519'"
                     " AND trade_date = ?", (FIRST,))
    _patch_fetch(monkeypatch, depth, {
        "600519": [_row("2005-01-04", 50.0, 80.0), _row(FIRST, 125.0, 200.0)]})
    res = depth.backfill_one("600519", FIRST, "2005-01-01")
    assert _get("600519", "2005-01-04")["close"] == 50.0   # 未缩放
    assert res["status"] == "done"


def test_qfq_ratio_guard_returns_one_on_bad_bridge():
    from scripts.backfill_price_depth import _qfq_ratio

    class R(dict):
        __getattr__ = dict.get

    assert _qfq_ratio(None) == 1.0
    assert _qfq_ratio(R(close=None, close_adj=200.0)) == 1.0
    assert _qfq_ratio(R(close=100.0, close_adj=0.0)) == 1.0
    assert _qfq_ratio(R(close=100.0, close_adj=200.0)) == 0.5


# ── 3. 四态、dry-run、汇总 ─────────────────────────────────────────────────

def test_run_marks_done_with_first_date_as_covered(db, monkeypatch):
    import scripts.backfill_price_depth as depth
    _seed("600519")
    _patch_fetch(monkeypatch, depth, {
        "600519": [_row(EARLY, 50.0, 80.0), _row(FIRST, 125.0, 200.0)]})
    out = depth.run(sleep=0)
    assert out["done"] == 1 and out["rows_added"] == 1
    assert out["remaining"] == 0 and out["seam_mismatch"] == 0
    with db_mod.db_conn() as conn:
        row = conn.execute("SELECT status, covered_report_date FROM backfill_progress"
                           " WHERE task='price_depth' AND code='600519'").fetchone()
    assert row["status"] == "done" and row["covered_report_date"] == FIRST


def test_ipo_later_than_target_is_no_data_not_partial(db, monkeypatch):
    """上市晚于 target_start -> 源确实没有那段，记 no_data（不是限流，不该重试）。"""
    import scripts.backfill_price_depth as depth
    _seed("600519")
    _patch_fetch(monkeypatch, depth, {"600519": []})
    out = depth.run(sleep=0)
    assert out["no_data"] == 1 and out["partial"] == 0
    assert out["rows_added"] == 0
    with db_mod.db_conn() as conn:
        assert conn.execute("SELECT status FROM backfill_progress"
                            " WHERE task='price_depth'").fetchone()[0] == "no_data"


def test_fetch_exception_is_partial_and_retried_next_run(db, monkeypatch):
    import scripts.backfill_price_depth as depth
    _seed("600519")
    _patch_fetch(monkeypatch, depth, {"600519": RuntimeError("限流")})
    out = depth.run(sleep=0, max_consecutive_failures=5)
    assert out["partial"] == 1 and out["aborted"] is False
    assert out["errors"][0][0] == "600519"
    _patch_fetch(monkeypatch, depth, {
        "600519": [_row(EARLY, 50.0, 80.0), _row(FIRST, 125.0, 200.0)]})
    out2 = depth.run(sleep=0)                    # partial 不记 covered -> 下轮自动重试
    assert out2["targets"] == 1 and out2["done"] == 1


def test_consecutive_failures_abort_without_writing_progress_rows(db, monkeypatch):
    import scripts.backfill_price_depth as depth
    for i in range(6):
        _seed(f"60000{i}")
    _patch_fetch(monkeypatch, depth, {})
    err = RuntimeError("整个源挂了")
    _patch_fetch(monkeypatch, depth, {f"60000{i}": err for i in range(6)})
    out = depth.run(sleep=0, max_consecutive_failures=3)
    assert out["aborted"] is True and out["partial"] == 3


def test_dry_run_writes_nothing_but_reports(db, monkeypatch):
    import scripts.backfill_price_depth as depth
    _seed("600519")
    _patch_fetch(monkeypatch, depth, {
        "600519": [_row(EARLY, 50.0, 80.0), _row(FIRST, 125.0, 200.0)]})
    out = depth.run(sleep=0, dry_run=True)
    assert out["rows_added"] == 1 and out["dry_run"] is True
    assert _get("600519", EARLY) is None
    with db_mod.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM backfill_progress").fetchone()[0] == 0


def test_task_isolation_from_price_history(db, monkeypatch):
    """进度表按 task 隔离：price_history 的 done 不会让深度回填误跳过。"""
    import scripts.backfill_price_depth as depth
    _seed("600519")
    db_mod.FinancialHistoryDAO().mark_backfill_done("price_history", "600519", 2, "done")
    _patch_fetch(monkeypatch, depth, {
        "600519": [_row(EARLY, 50.0, 80.0), _row(FIRST, 125.0, 200.0)]})
    assert depth.run(sleep=0)["done"] == 1


def test_cli_exit_codes_and_status(db, monkeypatch, capsys):
    import scripts.backfill_price_depth as depth
    _seed("600519")
    _patch_fetch(monkeypatch, depth, {"600519": []})
    assert depth.main(["--sleep", "0"]) == 0        # no_data 不是失败
    payload = json.loads(_last_json(capsys))
    assert payload["no_data"] == 1
    assert depth.main(["--status"]) == 0
    assert json.loads(_last_json(capsys))["no_data"] == 1
    _seed("000001")
    _patch_fetch(monkeypatch, depth, {"000001": RuntimeError("东财抽风")})
    assert depth.main(["--codes", "000001", "--sleep", "0"]) == 1   # partial -> 非 0


def _last_json(capsys):
    out = capsys.readouterr().out.strip().splitlines()
    return [l for l in out if l.startswith("{")][-1]


def test_script_has_no_chdir_and_no_raw_sqlite():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2]
           / "scripts" / "backfill_price_depth.py").read_text(encoding="utf-8")
    assert "os.chdir" not in src
    assert "import sqlite3" not in src      # 读写全走 DAO / db_conn
