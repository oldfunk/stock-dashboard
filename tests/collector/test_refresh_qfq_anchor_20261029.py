"""`scripts/refresh_qfq_anchor.py` 回归测试（批6-b，2026-10-29）。

覆盖脚本对外的四条承诺：
1. 窗口内重锚到新值，`close_qfq` 恒镜像 `close`；
2. **部分重锚**：窗口外旧行一字不动；
3. **列保护**：抓取缺 open/high/low 时不得把现网值抹成 NULL；
4. **fail-closed + 断点**：抓取失败只记 partial、不写库、下次自动重试；
   done 记 covered=锚点日，同锚点跳过、新锚点重新入选；--dry-run 零写入。
"""

import pytest

from src.models import database as db_mod

END = "2026-09-30"          # 本次锚点日
OUTSIDE = "2026-08-01"      # 90 天窗口之外（days=30 时）
INSIDE = ["2026-09-01", "2026-09-02", END]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "q.db"))
    db_mod.init_database()
    return db_mod


def _seed(code="600519"):
    """种一行窗口外 + 三行窗口内；老 close 一律 100，老 open 一律 90。"""
    rows = []
    for d in [OUTSIDE] + INSIDE:
        rows.append({"trade_date": d, "open": 90.0, "close": 100.0,
                     "high": 110.0, "low": 80.0, "volume": 1000.0,
                     "amount": 1e5, "turnover": 0.5,
                     "close_raw": 120.0, "close_adj": 240.0})
    db_mod.KlineDAO().upsert_many(code, rows)


def _get(code, day):
    with db_mod.db_conn() as conn:
        r = conn.execute("SELECT * FROM kline_daily WHERE code = ? "
                         "AND trade_date = ?", (code, day)).fetchone()
    return dict(r) if r else None


def _progress(code, task="qfq_anchor"):
    with db_mod.db_conn() as conn:
        r = conn.execute("SELECT * FROM backfill_progress WHERE task = ? "
                         "AND code = ?", (task, code)).fetchone()
    return dict(r) if r else None


def _fake_fetch(scale=1.1, drop_open=False, dates=None):
    """模拟 fetch_kline_sina_adjusted：返回 merge_price_model 形状的行。"""
    def fetch(code, start_date):
        out = []
        for d in (dates or INSIDE):
            rec = {"trade_date": d, "open": 90.0 * scale,
                   "close": 100.0 * scale, "high": 110.0 * scale,
                   "low": 80.0 * scale, "volume": 2000.0, "amount": 2e5,
                   "turnover": 0.6, "close_raw": 120.0, "close_adj": 240.0}
            if drop_open:
                rec["open"] = None
            out.append(rec)
        return out
    return fetch


def test_reanchors_window_rows_and_mirrors_close_qfq(db):
    from scripts.refresh_qfq_anchor import run
    _seed()
    s = run(days=30, codes=["600519"], sleep=0, fetch=_fake_fetch(1.1))
    assert s["done"] == 1 and s["partial"] == 0
    assert s["anchor"] == END
    r = _get("600519", END)
    assert r["close"] == pytest.approx(110.0)
    assert r["close_qfq"] == r["close"], "契约：close_qfq 必须镜像 close"
    assert r["close_raw"] == pytest.approx(120.0)   # 与锚点无关，不被改坏


def test_partial_reanchor_leaves_older_rows_untouched(db):
    """如实声明的"部分重锚"：窗口外旧行保持老锚，一个字都不动。"""
    from scripts.refresh_qfq_anchor import run
    _seed()
    run(days=30, codes=["600519"], sleep=0, fetch=_fake_fetch(1.1))
    old = _get("600519", OUTSIDE)
    assert old["close"] == pytest.approx(100.0), "窗口外不得被重写"
    assert old["open"] == pytest.approx(90.0)


def test_missing_fetched_open_does_not_blank_stored_value(db):
    """列保护：抓取这次没给 open，也不能把线上已有的 90.0 抹成 NULL。

    `upsert_many` 对 open/close/high/low/volume/amount 是无条件覆写，
    脚本必须先用现值兜底。
    """
    from scripts.refresh_qfq_anchor import run
    _seed()
    run(days=30, codes=["600519"], sleep=0,
        fetch=_fake_fetch(1.1, drop_open=True))
    r = _get("600519", END)
    assert r["close"] == pytest.approx(110.0), "抓到的新价照写"
    assert r["open"] == pytest.approx(90.0), "缺值必须回退到现值，不得为 NULL"
    assert r["high"] == pytest.approx(121.0)


def test_fetch_failure_writes_nothing_and_marks_partial(db):
    """fail-closed：抓取抛异常 → 一条都不写，老 close 保持原样。"""
    from scripts.refresh_qfq_anchor import run

    def boom(code, start_date):
        raise RuntimeError("Sina 抓取失败: 空数据")
    _seed()
    s = run(days=30, codes=["600519"], sleep=0, fetch=boom)
    assert s["partial"] == 1 and s["done"] == 0
    assert _get("600519", END)["close"] == pytest.approx(100.0), "半套数据不得覆盖"
    p = _progress("600519")
    assert p["status"] == "partial"
    assert p["covered_report_date"] is None, "partial 不写 covered，下次才会重试"


def test_empty_fetch_is_partial_not_done(db):
    from scripts.refresh_qfq_anchor import run
    _seed()
    s = run(days=30, codes=["600519"], sleep=0,
            fetch=lambda code, start: [])
    assert s["partial"] == 1
    assert _get("600519", END)["close"] == pytest.approx(100.0)
    assert _progress("600519")["status"] == "partial"


def test_same_anchor_skipped_new_anchor_reselected(db):
    """断点续跑语义：同锚点幂等跳过；新交易日到来后自动重新入选。"""
    from scripts.refresh_qfq_anchor import pending_codes, run
    _seed()
    with db_mod.db_conn() as conn:
        assert pending_codes(conn, END, days=30) == ["600519"]
    run(days=30, sleep=0, fetch=_fake_fetch(1.1))
    with db_mod.db_conn() as conn:
        assert pending_codes(conn, END, days=30) == [], "已覆盖本锚点，应跳过"
        assert pending_codes(conn, END, days=30, force=True) == ["600519"]

    # 新交易日：锚点前移 → 覆盖不到新锚点，重新入选
    db_mod.KlineDAO().upsert_many("600519", [{
        "trade_date": "2026-10-08", "open": 90.0, "close": 100.0,
        "high": 110.0, "low": 80.0, "volume": 1.0, "amount": 1.0}])
    with db_mod.db_conn() as conn:
        assert pending_codes(conn, "2026-10-08", days=30) == ["600519"]


def test_dry_run_writes_nothing(db):
    from scripts.refresh_qfq_anchor import run
    _seed()
    called = []
    s = run(days=30, codes=["600519"], dry_run=True, sleep=0,
            fetch=lambda code, start: called.append(code) or [])
    assert called == [], "dry-run 不得发抓取"
    assert s["skipped"] == 1 and s["done"] == 0
    assert s["written"] == 3, "dry-run 报告窗口内行数供人估工作量"
    assert _get("600519", END)["close"] == pytest.approx(100.0)
    assert _progress("600519") is None, "dry-run 不记进度"


def test_pending_codes_only_covers_codes_with_rows_in_window(db):
    """目标集判据：窗口内有行才入选（停牌但窗口内有成交的票不能被漏掉）。"""
    from scripts.refresh_qfq_anchor import pending_codes
    _seed("600519")
    db_mod.KlineDAO().upsert_many("000001", [{
        "trade_date": OUTSIDE, "open": 10.0, "close": 10.0, "high": 11.0,
        "low": 9.0, "volume": 1.0, "amount": 1.0}])
    with db_mod.db_conn() as conn:
        assert pending_codes(conn, END, days=30) == ["600519"]
        # 窗口拉到覆盖到 08-01，两只都该进来
        assert pending_codes(conn, END, days=90) == ["000001", "600519"]


def test_run_on_empty_db_reports_no_anchor(db):
    from scripts.refresh_qfq_anchor import run
    s = run(days=30, sleep=0, fetch=_fake_fetch())
    assert s["anchor"] is None and s["targets"] == 0
