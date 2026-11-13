"""批20-b：qfq 重锚的**节奏**与接线（2026-11-12，不打网络）。

派工单要求"滚动窗口 + 限量/限时/进度表续跑"，并警告"5,513 只 × 2 拉会打爆上游"。
产线只读实测（锚点 2026-11-11、90 天窗口）给了两条决定设计的数字：

- 窗口内 **5,514 只 / 281,028 行**，其中 **320 只北交所**——Sina 供不了，
  每只三次退避 5/10/20s ≈ 35s，不记账就是每周白烧 3.1 小时；
- 真正带**多个锚点**（=序列里有台阶）的只有 **24 只**，正是每日增量在写的那批；
  其余 5,490 只单锚。收益 close[t]/close[t-1] 会把同一 scale 约掉，
  所以单锚票重锚前后收益率一字不变——**限量预算必须先花在多锚票上**。

本文件钉：漂移优先排序、限量、限时、熔断、结构性跳过不写进度也不计熔断、
停在任意处都能续跑、配置真的驱动预算、以及周六维护线程真的按序调到这一段。
"""

import pytest

from src.collector import qfq_anchor as qa
from src.collector.akshare_fetcher import SourceUnsupportedError
from src.models import database as db_mod

END = "2026-09-30"
OLD = ["2026-09-01", "2026-09-02"]
NEW = ["2026-09-29", END]
ALL = OLD + NEW


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "q.db"))
    db_mod.init_database()
    return db_mod


def _row(d, close, adj=240.0):
    return {"trade_date": d, "open": close * 0.9, "close": close,
            "high": close * 1.1, "low": close * 0.8, "volume": 1000.0,
            "amount": 1e5, "turnover": 0.5, "close_raw": close * 1.2,
            "close_adj": adj}


def _seed(code, closes=None, adj=240.0):
    """默认单锚（全 100）；给 closes 就按日期映射写，可造出多锚台阶。"""
    closes = closes or {d: 100.0 for d in ALL}
    db_mod.KlineDAO().upsert_many(
        code, [_row(d, c, adj) for d, c in closes.items() if c is not None])


def _seed_no_adj(code):
    db_mod.KlineDAO().upsert_many(code, [
        {"trade_date": d, "open": 90.0, "close": 100.0, "high": 110.0,
         "low": 80.0, "volume": 1.0, "amount": 1.0} for d in ALL])


def _scales(code):
    """窗口内 close/close_adj 的不同取值个数 = 该股带了几种锚点。"""
    with db_mod.db_conn() as conn:
        return conn.execute(
            "SELECT COUNT(DISTINCT CASE WHEN close>0 AND close_adj>0"
            " THEN ROUND(close/close_adj,6) END) FROM kline_daily"
            " WHERE code=? AND trade_date>=? AND trade_date<=?",
            (code, "2026-08-31", END)).fetchone()[0]


def _uniform(scale=1.1, dates=None):
    def fetch(code, start):
        return [_row(d, 100.0 * scale) for d in (dates or ALL)]
    return fetch


def _boom(code, start):
    raise RuntimeError("Sina 抓取失败")


def _bj(code, start):
    raise SourceUnsupportedError(f"Sina 不支持北交所 {code}")


class FakeClock:
    """替掉 qa.time：monotonic 可控、sleep 只记账不真睡（也不污染全局 time）。"""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _progress(code):
    with db_mod.db_conn() as conn:
        r = conn.execute("SELECT * FROM backfill_progress WHERE task='qfq_anchor'"
                         " AND code=?", (code,)).fetchone()
    return dict(r) if r else None


# ── 漂移优先：限量预算先花在真的有台阶的票上 ──────────────────────────

def test_drifted_codes_sort_first_and_limit_picks_them(db):
    _seed("000001", {OLD[0]: 100.0, OLD[1]: 100.0, NEW[0]: 110.0, NEW[1]: 110.0})
    _seed("600519")                      # 单锚
    _seed_no_adj("000002")               # close_adj 全 NULL → scales=0
    assert (_scales("000001"), _scales("600519"), _scales("000002")) == (2, 1, 0)
    with db_mod.db_conn() as conn:
        assert qa.pending_codes(conn, END, days=30) == [
            "000001", "600519", "000002"], "多锚先修，测不出锚的排最后但仍在集内"
        assert qa.pending_codes(conn, END, days=30, limit=1) == ["000001"]
        assert qa.pending_rows(conn, END, days=30)[0] == ("000001", 2)


def test_reanchor_actually_removes_the_step(db):
    """物理证据：重锚前窗口内 2 个 scale（台阶），重锚后收敛到 1 个。"""
    _seed("000001", {OLD[0]: 100.0, OLD[1]: 100.0, NEW[0]: 110.0, NEW[1]: 110.0})
    assert _scales("000001") == 2
    s = qa.run(days=30, codes=["000001"], sleep=0, fetch=_uniform(1.1))
    assert s["done"] == 1
    assert _scales("000001") == 1, "重锚后必须只剩一个锚点"


def test_summary_reports_how_many_targets_were_drifted(db):
    _seed("000001", {OLD[0]: 100.0, OLD[1]: 100.0, NEW[0]: 110.0, NEW[1]: 110.0})
    _seed("600519")
    s = qa.run(days=30, sleep=0, fetch=_uniform())
    assert s["drifted_targets"] == 1 and s["targets"] == 2
    # 显式 codes 不查漂移，如实报 None 而不是假装 0
    assert qa.run(days=30, codes=["600519"], sleep=0,
                  fetch=_uniform())["drifted_targets"] is None


# ── 限量 / 限时 / 续跑 ────────────────────────────────────────────────

def test_limit_caps_the_batch_and_reports_remaining(db):
    for c in ("000001", "000002", "000004", "000005", "600519"):
        _seed(c)
    s = qa.run(days=30, limit=2, sleep=0, fetch=_uniform())
    assert (s["targets"], s["done"], s["remaining"]) == (2, 2, 3)
    assert s["budget_hit"] is False and s["aborted"] is False
    assert s["elapsed_seconds"] >= 0


def test_time_budget_stops_mid_batch(db, monkeypatch):
    for c in ("000001", "000002", "000004", "000005", "600519"):
        _seed(c)
    clock = FakeClock()
    monkeypatch.setattr(qa, "time", clock)

    def slow(code, start):
        clock.t += 10.0          # 每只 10s
        return _uniform()(code, start)

    s = qa.run(days=30, sleep=0, fetch=slow, max_seconds=25)
    assert s["budget_hit"] is True
    assert (s["done"], s["remaining"]) == (3, 2), "25s 预算只够 3 只（第 3 只后到点）"
    assert s["targets"] == 5


def test_budget_stop_is_resumable_next_run(db, monkeypatch):
    for c in ("000001", "000002", "000004", "000005", "600519"):
        _seed(c)
    clock = FakeClock()
    monkeypatch.setattr(qa, "time", clock)

    def slow(code, start):
        clock.t += 10.0
        return _uniform()(code, start)

    first = qa.run(days=30, sleep=0, fetch=slow, max_seconds=25)
    assert first["remaining"] == 2
    clock.t = 0.0
    second = qa.run(days=30, sleep=0, fetch=slow, max_seconds=25)
    assert (second["done"], second["remaining"]) == (2, 0), "下一班车接着跑完"
    assert second["budget_hit"] is False


def test_same_anchor_skipped_after_full_coverage(db):
    _seed("600519")
    qa.run(days=30, sleep=0, fetch=_uniform())
    with db_mod.db_conn() as conn:
        assert qa.pending_codes(conn, END, days=30) == []
    s = qa.run(days=30, sleep=0, fetch=_uniform())
    assert s["targets"] == 0 and s["done"] == 0


# ── 熔断：上游抽风 vs 结构性跳过 ──────────────────────────────────────

def test_consecutive_failures_abort_and_leave_targets_pending(db):
    for c in ("000001", "000002", "000004", "000005", "600519"):
        _seed(c)
    s = qa.run(days=30, sleep=0, fetch=_boom, max_consecutive_failures=2)
    assert s["aborted"] is True
    assert (s["done"], s["partial"], s["remaining"]) == (0, 2, 5)
    assert _progress("000001")["status"] == "partial"
    assert _progress("000001")["covered_report_date"] is None, "partial 不写 covered"
    assert _progress("000004") is None, "熔断后没轮到的票不写进度"


def test_success_resets_the_failure_counter(db):
    for c in ("000001", "000002", "000004"):
        _seed(c)
    order = ["000001", "000002", "000004"]

    def flaky(code, start):
        if code == "000002":
            return _uniform()(code, start)
        raise RuntimeError("Sina 抓取失败")

    s = qa.run(days=30, codes=order, sleep=0, fetch=flaky,
               max_consecutive_failures=2)
    assert s["aborted"] is False, "失败-成功-失败 不该触发连续 2 次熔断"
    assert (s["done"], s["partial"]) == (1, 2)


def test_bj_skip_writes_no_progress_and_does_not_feed_breaker(db):
    """批13-d 同一口径：能力边界不是失败，记 partial/no_data 都是错的。"""
    for c in ("920000", "920001", "920002"):
        _seed(c)
    _seed("600519")
    s = qa.run(days=30, codes=["920000", "920001", "920002", "600519"],
               sleep=0, fetch=_bj_or_ok, max_consecutive_failures=2)
    assert s["aborted"] is False, "3 只结构性跳过 + 阈值 2 不该中止"
    assert (s["skipped_unsupported"], s["done"], s["partial"]) == (3, 1, 0)
    for c in ("920000", "920001", "920002"):
        assert _progress(c) is None, "结构性跳过不写进度（否则堵死换源重跑）"
    assert _progress("600519")["status"] == "done"


def _bj_or_ok(code, start):
    if code.startswith("92"):
        raise SourceUnsupportedError(f"Sina 不支持北交所 {code}")
    return _uniform()(code, start)


def test_bj_skip_burns_no_backoff_sleep(db, monkeypatch):
    for c in ("920000", "920001", "920002"):
        _seed(c)
    clock = FakeClock()
    monkeypatch.setattr(qa, "time", clock)
    s = qa.run(days=30, sleep=1.0, fetch=_bj)
    assert s["skipped_unsupported"] == 3
    assert clock.sleeps == [], "跳过不该睡限速间隔（产线 320 只 BJ 会白等）"


def test_bj_codes_still_reselected_next_run(db):
    _seed("920000")
    qa.run(days=30, sleep=0, fetch=_bj)
    with db_mod.db_conn() as conn:
        assert qa.pending_codes(conn, END, days=30) == ["920000"]


# ── 配置驱动（批20-c 的入口契约）──────────────────────────────────────

def test_weekly_entry_passes_config_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(qa, "run", lambda **kw: seen.update(kw) or {"done": 0})
    qa.run_weekly_qfq_anchor({"qfq_anchor": {
        "days": 45, "batch": 7, "max_seconds": 120, "sleep": 0.2,
        "max_consecutive_failures": 3}})
    assert seen == {"days": 45, "limit": 7, "max_seconds": 120.0,
                    "sleep": 0.2, "max_consecutive_failures": 3}


def test_weekly_entry_defaults_when_config_absent(monkeypatch):
    seen = {}
    monkeypatch.setattr(qa, "run", lambda **kw: seen.update(kw) or {"done": 0})
    qa.run_weekly_qfq_anchor({})
    assert seen == {"days": 90, "limit": 60, "max_seconds": 900.0,
                    "sleep": 1.0, "max_consecutive_failures": 5}
    qa.run_weekly_qfq_anchor(None)
    assert seen["limit"] == 60


@pytest.mark.parametrize("batch", [0, -1])
def test_batch_zero_disables_the_stage(monkeypatch, batch):
    called = []
    monkeypatch.setattr(qa, "run", lambda **kw: called.append(kw) or {})
    s = qa.run_weekly_qfq_anchor({"qfq_anchor": {"batch": batch}})
    assert called == [], "batch<=0 必须一段都不跑"
    assert s["skipped_by_config"] is True and s["targets"] == 0


# ── 周六维护线程真的按序调到这一段 ────────────────────────────────────

@pytest.fixture
def body(tmp_path, monkeypatch):
    """维护体各步换成记名字的假件；直接调 _run_weekly_refresh（不需要真线程）。"""
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    import src.scheduler as s
    import src.collector.dividend_fetcher as df
    import src.outcome_service as osvc
    from src.collector import trading_calendar as tc
    from src.collector import weekly_deep_refresh as wdr
    called = []
    monkeypatch.setattr(df, "ensure_dividend_data",
                        lambda cfg=None: called.append("dividend"))
    monkeypatch.setattr(tc, "backfill_trading_calendar",
                        lambda: called.append("calendar") or {"future": 1})
    monkeypatch.setattr(osvc, "refresh_benchmarks",
                        lambda cfg=None: called.append("bench"))
    monkeypatch.setattr(osvc, "refresh_outcomes",
                        lambda cfg=None: called.append("outcome"))
    monkeypatch.setattr(wdr, "run_weekly_deep_refresh",
                        lambda cfg=None: called.append("deep"))
    return s, s.MarketScheduler(), called, monkeypatch


def test_q_stage_runs_before_outcomes(body):
    s, m, called, mp = body
    mp.setattr(qa, "run_weekly_qfq_anchor",
               lambda cfg=None: (called.append("qfq"),
                                 {"anchor": END, "done": 1, "partial": 0,
                                  "skipped_unsupported": 0, "remaining": 0})[1])
    m._run_weekly_refresh({}, {"enabled": True})
    assert called == ["dividend", "calendar", "qfq", "bench", "outcome", "deep"]
    assert called.index("qfq") < called.index("outcome"), (
        "前向收益算出即冻结，价格口径必须先统一")


def test_q_stage_gets_full_cfg_not_outcomes_cfg(body):
    s, m, called, mp = body
    seen = {}
    mp.setattr(qa, "run_weekly_qfq_anchor",
               lambda cfg=None: seen.update(cfg=cfg) or {"done": 0})
    cfg = {"qfq_anchor": {"batch": 3}, "outcomes": {"enabled": True}}
    m._run_weekly_refresh(cfg, cfg["outcomes"])
    assert seen["cfg"] is cfg, "传错成 out_cfg 就读不到 qfq_anchor 段"


def test_q_stage_failure_does_not_skip_later_steps(body):
    s, m, called, mp = body

    def explode(cfg=None):
        called.append("qfq")
        raise RuntimeError("Sina 全挂")

    mp.setattr(qa, "run_weekly_qfq_anchor", explode)
    m._run_weekly_refresh({}, {"enabled": True})
    assert called == ["dividend", "calendar", "qfq", "bench", "outcome", "deep"]
    assert m._weekly_in_progress is False


def test_q_stage_still_runs_when_outcomes_disabled(body):
    """outcomes.enabled=false 只该关掉收益段，不该顺手把重锚也关掉。"""
    s, m, called, mp = body
    mp.setattr(qa, "run_weekly_qfq_anchor",
               lambda cfg=None: called.append("qfq") or {"done": 0})
    m._run_weekly_refresh({}, {"enabled": False})
    assert called == ["dividend", "calendar", "qfq", "deep"]