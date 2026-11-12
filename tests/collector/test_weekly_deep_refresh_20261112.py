"""批19-b 用例（2026-11-12）：周末全量刷新真的发生，且分批/限时/断点续跑。

派工单点的死条件：``orchestrator`` 里 ``is_weekend_full = weekday()==5``，而流水线
只在工作日 15:30 跑（``scheduler._check_daily_pipeline`` 周末直接 return）→
``force_full`` 永远 False，周末全量从未发生。

本件选**选项①**（挂进周六维护线程），并守四件事：
1. 平日流水线不再自己判周末（那条判定是死的），全量走周维护；
2. 周维护路径**真的**以 ``force_full=True`` 调采集器（验收标准原文）；
3. 目标集与平日同一套窗口口径（近 EM_DEEP_WINDOW 期），但**不受冷却限制**——
   周六就是给平日拉不上的股票再一次机会；
4. 打不爆上游：限量 + 限时 + 进度表断点续跑 + 连续失败中止 + 耗时可读到。
"""

import time
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from src.models import database as db_mod

FIELDS = ("interest_coverage", "fcf")


def _dates(n, end="2026-09-30"):
    """从 end 往前推 n 个季度（新→旧）。"""
    y, m, d = (int(x) for x in end.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
        m -= 3
        if m <= 0:
            m += 12
            y -= 1
    return out


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "wk.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _seed(code, dates, *, ic=True, fcf=True):
    db_mod.FinancialHistoryDAO().batch_save([
        {"stock_code": code, "report_date": d, "roe": 10.0,
         **({"interest_coverage": 9.0} if ic else {}),
         **({"fcf": 700.0} if fcf else {})}
        for d in dates])


def _marks(task):
    with db_mod.db_conn() as conn:
        return {r["code"]: (r["status"], r["covered_report_date"]) for r in conn.execute(
            "SELECT code, status, covered_report_date FROM backfill_progress"
            " WHERE task=?", (task,))}


# ── 1. DAO 目标集：窗口口径与采集侧一致 ────────────────────────────────────

def test_window_matches_collector_predicate(db):
    """近 8 期填满、更早 4 期永远空 → 不入选；窗口放到 12 期才入选。

    与 ``_em_sheets_needed`` 同一口径：老期缺口不许绑架请求（批16-a/17-a）。
    """
    from src.collector.akshare_fetcher import EM_DEEP_FIELDS, _em_sheets_needed
    dao = db_mod.FinancialHistoryDAO()
    dates = _dates(12)
    recent, old = dates[:8], dates[8:]
    _seed("600519", recent)
    _seed("600519", old, ic=False, fcf=False)
    filled = {"interest_coverage": set(recent), "fcf": set(recent)}
    assert _em_sheets_needed(dates, filled) == (False, False)
    assert dao.targets_missing_deep_fields(EM_DEEP_FIELDS, 8) == []
    assert dao.targets_missing_deep_fields(EM_DEEP_FIELDS, 12) == [
        ("600519", dates[0])]


def test_recent_gap_is_selected_and_field_list_is_single_source(db):
    from src.collector.akshare_fetcher import EM_DEEP_FIELDS
    dao = db_mod.FinancialHistoryDAO()
    dates = _dates(4)
    _seed("600519", dates)                                   # 齐全
    _seed("000001", dates, ic=False)                         # 只缺 ic
    _seed("600036", dates, fcf=False)                        # 只缺 fcf
    assert dao.targets_missing_deep_fields(EM_DEEP_FIELDS, 8) == [
        ("000001", dates[0]), ("600036", dates[0])]
    # 判据列与查询列同一份：只查 ic 时，只缺 fcf 的那只不该入选
    assert dao.targets_missing_deep_fields(("interest_coverage",), 8) == [
        ("000001", dates[0])]


@pytest.mark.parametrize("bad", ["password", "1=1; DROP TABLE financial_history",
                                 "interest_coverage, fcf"])
def test_fields_outside_whitelist_are_rejected(db, bad):
    """列名进 f-string SQL，白名单是唯一防线（批8 P1-2 同一条规矩）。"""
    with pytest.raises(ValueError):
        db_mod.FinancialHistoryDAO().targets_missing_deep_fields((bad,), 8)


@pytest.mark.parametrize("window", [0, -3])
def test_window_not_positive_clamped_to_one_period(db, window):
    """与 ``_em_sheets_needed`` 的钳制一致（批17-a H3 建议①）。"""
    dao = db_mod.FinancialHistoryDAO()
    dates = _dates(3)
    _seed("600519", [dates[0]])
    _seed("600519", dates[1:], ic=False, fcf=False)
    assert dao.targets_missing_deep_fields(FIELDS, window) == []
    _seed("000001", dates, ic=False, fcf=False)
    assert dao.targets_missing_deep_fields(FIELDS, window) == [
        ("000001", dates[0])]


def test_progress_excludes_done_and_reenters_on_new_period(db):
    dao = db_mod.FinancialHistoryDAO()
    dates = _dates(4)
    _seed("600519", dates, ic=False)
    _seed("000001", dates, ic=False)
    dao.mark_backfill_done("em_full_gap", "600519", 0, "no_data", dates[0])
    dao.mark_backfill_done("em_full_gap", "000001", 0, "partial", None)
    assert dao.targets_missing_deep_fields(FIELDS, 8, task="em_full_gap") == [
        ("000001", dates[0])]
    newer = _dates(1, end="2026-12-31")[0]
    _seed("600519", [newer], ic=False)
    assert dao.targets_missing_deep_fields(FIELDS, 8, task="em_full_gap") == [
        ("000001", dates[0]), ("600519", newer)]


def test_limit_offset_and_order(db):
    dao = db_mod.FinancialHistoryDAO()
    dates = _dates(2)
    for i in range(5):
        _seed(f"60000{i}", dates, ic=False)
    assert dao.targets_missing_deep_fields(FIELDS, 8, limit=2) == [
        ("600000", dates[0]), ("600001", dates[0])]
    assert dao.targets_missing_deep_fields(FIELDS, 8, limit=2, offset=2) == [
        ("600002", dates[0]), ("600003", dates[0])]


# ── 2. EM 全量段：真的 force_full、限量、限时、续跑 ─────────────────────────

@pytest.fixture
def em(db, monkeypatch):
    """假采集器：记录每次调用的代码与 force_full，可配置"拉到/拉空/抛异常"。"""
    from src.collector import weekly_deep_refresh as mod
    import src.collector.akshare_fetcher as af
    calls = []
    state = {"fill": set(), "empty": set(), "boom": set()}

    def fake_collect(stocks, force_full=False, incremental_days=60):
        codes = [s["code"] for s in stocks]
        calls.append((codes, force_full))
        code = codes[0]
        if code in state["boom"]:
            raise TimeoutError("上游无响应")
        if code in state["fill"]:
            with db_mod.db_conn() as conn:
                conn.execute("UPDATE financial_history SET interest_coverage=9.0,"
                             " fcf=700.0 WHERE stock_code=?", (code,))
        return {} if code in state["empty"] else {code: True}

    monkeypatch.setattr(af, "collect_historical_financial_data", fake_collect)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod, calls, state


def test_weekly_em_really_calls_force_full(em):
    mod, calls, state = em
    dates = _dates(4)
    _seed("600519", dates, ic=False)
    _seed("000001", dates, fcf=False)
    state["fill"] = {"600519", "000001"}
    result = mod.run_em_full_refresh(sleep=0)
    assert result["processed"] == 2
    assert calls == [(["000001"], True), (["600519"], True)], \
        "周维护必须以 force_full=True 逐只调采集器（验收标准原文）"
    assert result["status_counts"] == {"done": 2, "partial": 0, "no_data": 0}


def test_daily_cooldown_does_not_block_the_weekly_full_pull(em):
    """平日冷却（em_deep_gap=no_data）的股票，周六仍要给一次全量机会。"""
    mod, calls, state = em
    dates = _dates(4)
    _seed("600519", dates, ic=False)
    db_mod.FinancialHistoryDAO().mark_backfill_done(
        "em_deep_gap", "600519", 0, "no_data", dates[0])
    state["fill"] = {"600519"}
    assert mod.run_em_full_refresh(sleep=0)["processed"] == 1
    assert calls == [(["600519"], True)]


def test_batch_limit_bounds_upstream_requests(em):
    mod, calls, state = em
    dates = _dates(2)
    for i in range(10):
        _seed(f"60000{i}", dates, ic=False)
    result = mod.run_em_full_refresh(limit=3, sleep=0)
    assert result["targets"] == 3 and result["processed"] == 3
    assert len(calls) == 3
    assert result["remaining"] == 7, "剩余要能读到，否则不知道还要跑几周"


def test_time_budget_stops_gracefully(em):
    mod, calls, state = em
    dates = _dates(2)
    for i in range(5):
        _seed(f"60000{i}", dates, ic=False)
    real = time.monotonic
    with patch.object(mod.time, "monotonic",
                      side_effect=[real()] + [real() + 100] * 20):
        result = mod.run_em_full_refresh(max_seconds=1, sleep=0)
    assert result["budget_hit"] is True and result["processed"] == 1


def test_progress_marks_make_it_resumable(em):
    mod, calls, state = em
    dates = _dates(2)
    for i in range(4):
        _seed(f"60000{i}", dates, ic=False)
    state["fill"] = {f"60000{i}" for i in range(4)}
    first = mod.run_em_full_refresh(limit=2, sleep=0)
    assert first["processed"] == 2 and first["remaining"] == 2
    second = mod.run_em_full_refresh(limit=2, sleep=0)
    assert second["processed"] == 2 and second["remaining"] == 0
    assert [c[0][0] for c in calls] == ["600000", "600001", "600002", "600003"]
    assert mod.run_em_full_refresh(limit=2, sleep=0)["targets"] == 0


@pytest.mark.parametrize("mode,expect_status,expect_covered", [
    ("fill", "done", True),        # 拉到了且缺口没了
    ("none", "no_data", True),     # 拉到了但上游就是没有这个字段
    ("empty", "partial", False),   # 骨架没出数 = 这次没取到，下次重试
    ("boom", "partial", False),    # 异常 = 没取全，下次重试
])
def test_status_matrix_and_covered(em, mode, expect_status, expect_covered):
    mod, calls, state = em
    dates = _dates(3)
    _seed("600519", dates, ic=False)
    if mode != "none":
        state[mode if mode != "fill" else "fill"] = {"600519"}
    result = mod.run_em_full_refresh(sleep=0)
    assert result["status_counts"][expect_status] == 1
    status, covered = _marks(mod.TASK_EM_FULL)["600519"]
    assert status == expect_status
    assert (covered == dates[0]) is expect_covered


def test_aborts_after_consecutive_failures(em):
    mod, calls, state = em
    dates = _dates(2)
    for i in range(6):
        _seed(f"60000{i}", dates, ic=False)
    state["boom"] = {f"60000{i}" for i in range(6)}
    result = mod.run_em_full_refresh(sleep=0, max_consecutive_failures=3)
    assert result["aborted"] is True and result["processed"] == 3
    assert result["status_counts"]["partial"] == 3


def test_elapsed_is_reported_for_pacing(em):
    """派工单要"测速"：耗时/只数必须能读到，批大小才有调整依据。"""
    mod, calls, state = em
    dates = _dates(2)
    _seed("600519", dates, ic=False)
    state["fill"] = {"600519"}
    logged = []

    class Spy:
        def info(self, msg, *a):
            logged.append(msg % a if a else str(msg))

        warning = error = debug = info

    mod.logger = Spy()
    try:
        result = mod.run_em_full_refresh(sleep=0)
    finally:
        import logging
        mod.logger = logging.getLogger(mod.__name__)
    assert result["elapsed_seconds"] >= 0
    assert any("耗时" in m and "剩余" in m for m in logged)


# ── 3. 周维护入口：两段都跑，预算来自配置 ──────────────────────────────────

def test_weekly_entry_runs_ic_then_em_with_config_budgets(db, monkeypatch):
    from src.collector import weekly_deep_refresh as mod
    seen = []
    monkeypatch.setattr(mod.ic_backfill, "run", lambda **kw: (
        seen.append(("ic", kw)), {"filled": 11, "remaining": 3})[1])
    monkeypatch.setattr(mod, "run_em_full_refresh", lambda **kw: (
        seen.append(("em", kw)), {"processed": 2, "remaining": 5})[1])
    out = mod.run_weekly_deep_refresh({"deep_refresh": {
        "ic_max_seconds": 120, "em_max_seconds": 60, "em_batch": 7, "sleep": 0.1}})
    assert [s[0] for s in seen] == ["ic", "em"], "先便宜的 ic 回填，再贵的 EM 全量"
    assert seen[0][1] == {"max_seconds": 120.0, "sleep": 0.1}
    assert seen[1][1] == {"limit": 7, "max_seconds": 60.0, "sleep": 0.1}
    assert out["ic"]["filled"] == 11 and out["em"]["processed"] == 2


def test_weekly_entry_defaults_without_config(db, monkeypatch):
    from src.collector import weekly_deep_refresh as mod
    seen = []
    monkeypatch.setattr(mod.ic_backfill, "run",
                        lambda **kw: (seen.append(("ic", kw)), {})[1])
    monkeypatch.setattr(mod, "run_em_full_refresh",
                        lambda **kw: (seen.append(("em", kw)), {})[1])
    mod.run_weekly_deep_refresh()
    assert seen[0][1]["max_seconds"] == mod.DEFAULT_IC_SECONDS
    assert seen[1][1]["limit"] == mod.DEFAULT_EM_BATCH
    assert seen[1][1]["max_seconds"] == mod.DEFAULT_EM_SECONDS


def test_zero_batch_turns_off_the_em_stage(db, monkeypatch):
    """上游限流时母方要能只留 ic 段：em_batch=0 就是"这段不跑"，不需要新开关。"""
    from src.collector import weekly_deep_refresh as mod
    import src.collector.akshare_fetcher as af
    calls = []
    monkeypatch.setattr(af, "collect_historical_financial_data",
                        lambda *a, **k: calls.append(a))
    dates = _dates(2)
    _seed("600519", dates, ic=False)
    monkeypatch.setattr(mod.ic_backfill, "run", lambda **kw: {"filled": 0})
    out = mod.run_weekly_deep_refresh({"deep_refresh": {"em_batch": 0}})
    assert out["em"]["processed"] == 0 and calls == []


# ── 4. 调度器：周维护路径真的调用它 ────────────────────────────────────────

def _saturday(hh=9, mm=5):
    d = date(2026, 11, 12)
    while d.weekday() != 5:
        d += timedelta(days=1)
    return datetime.combine(d, datetime.min.time().replace(hour=hh, minute=mm))


@pytest.fixture
def sched(db, monkeypatch):
    import src.scheduler as s
    import src.collector.dividend_fetcher as df
    from src.collector import trading_calendar as tc
    from src.collector import weekly_deep_refresh as wdr
    import src.outcome_service as osvc
    import src.config as cmod
    called = []
    started = []

    class FakeThread:
        """拦下真线程：否则维护体在后台跑完并把进行中闸清掉，断言就成了赛跑。"""

        def __init__(self, target=None, args=(), **kw):
            self.target, self.args = target, args

        def start(self):
            started.append(self)

    monkeypatch.setattr(s.threading, "Thread", FakeThread)
    monkeypatch.setattr(df, "ensure_dividend_data",
                        lambda cfg=None: called.append("dividend"))
    monkeypatch.setattr(tc, "backfill_trading_calendar",
                        lambda: (called.append("calendar"), {"future": 1})[1])
    monkeypatch.setattr(osvc, "refresh_benchmarks",
                        lambda cfg=None: called.append("bench"))
    monkeypatch.setattr(osvc, "refresh_outcomes",
                        lambda cfg=None: called.append("outcome"))
    monkeypatch.setattr(wdr, "run_weekly_deep_refresh",
                        lambda cfg=None: (called.append("deep"),
                                          {"ic": {"filled": 0}, "em": {"processed": 0}})[1])
    monkeypatch.setattr(cmod, "load_config", lambda: {"outcomes": {
        "refresh_weekday": 5, "refresh_time": "09:00", "enabled": True}})
    monkeypatch.setattr(s, "now_cn", lambda: _saturday())
    m = s.MarketScheduler()
    m._last_weekly_refresh = None
    return s, m, called, started


def test_saturday_maintenance_invokes_deep_refresh(sched):
    """验收标准：周维护路径真的调用全量（而不是只在 orchestrator 里判星期）。"""
    s, m, called, started = sched
    m._check_weekly_data_refresh()
    assert m._weekly_in_progress is True and called == []
    assert len(started) == 1
    started[0].target(*started[0].args)
    assert called == ["dividend", "calendar", "bench", "outcome", "deep"]
    assert m._weekly_in_progress is False


def test_deep_refresh_failure_does_not_block_or_stick(sched, monkeypatch):
    s, m, called, started = sched
    from src.collector import weekly_deep_refresh as wdr
    monkeypatch.setattr(wdr, "run_weekly_deep_refresh",
                        lambda cfg=None: (_ for _ in ()).throw(RuntimeError("炸")))
    m._run_weekly_refresh({}, {"enabled": True})
    assert called == ["dividend", "calendar", "bench", "outcome"]
    assert m._weekly_in_progress is False


def test_deep_refresh_receives_config(sched, monkeypatch):
    """预算要能从配置调，不然母方只能改代码才能限速。"""
    s, m, called, started = sched
    from src.collector import weekly_deep_refresh as wdr
    seen = {}
    monkeypatch.setattr(wdr, "run_weekly_deep_refresh",
                        lambda cfg=None: seen.update(cfg=cfg) or {
                            "ic": {"filled": 0}, "em": {"processed": 0}})
    cfg = {"deep_refresh": {"em_batch": 3}, "outcomes": {"enabled": True}}
    m._run_weekly_refresh(cfg, cfg["outcomes"])
    assert seen["cfg"] is cfg


# ── 5. 平日流水线：不再自己判周末（那条判定是死的） ────────────────────────

def _run_pipeline(mock_hist):
    import src.orchestrator as orch_mod
    cands = [{"code": "600519", "name": "A", "roe": 20.0}]
    with patch("src.collector.akshare_fetcher.run_collect_pipeline",
               return_value=cands), \
        patch.object(orch_mod, "StockSnapshotDAO") as snap, \
        patch.object(orch_mod, "PipelineProgressDAO"), \
        patch.object(orch_mod, "RunLogDAO"), \
        patch("src.collector.akshare_fetcher.collect_historical_financial_data",
              mock_hist), \
        patch("src.collector.akshare_fetcher.rebuild_financial_summaries"), \
        patch("src.collector.akshare_fetcher.enrich_financial_data"), \
        patch("src.collector.akshare_fetcher.fetch_tencent_batch", return_value=[]), \
        patch("src.collector.akshare_fetcher.fetch_sector_map", return_value={}), \
        patch("src.models.database.WatchlistDAO"), \
        patch("src.screener.value_screener.run_screener", return_value=[]):
        snap.return_value.count.return_value = 1
        orch_mod.run_collect_and_screen({})


def test_daily_pipeline_stays_incremental_even_on_saturday(db):
    """周六 15:30 手动触发也不该全量：全量归周维护线程（限量/限时/续跑）。

    把 datetime.now() 钉在周六：旧写法 ``weekday()==5`` 在这里会传 force_full=True，
    一次性打 771×6 个请求；新写法必须仍是增量。
    """
    from unittest.mock import Mock
    hist = Mock(return_value={})
    sat = datetime(2026, 11, 14, 15, 30)      # 周六
    with patch("datetime.datetime") as dt:
        dt.now.return_value = sat
        dt.strptime.side_effect = datetime.strptime
        _run_pipeline(hist)
    hist.assert_called_once()
    assert hist.call_args.kwargs.get("force_full", False) is False