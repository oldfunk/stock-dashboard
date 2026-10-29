"""N9（批7）：周六维护必须移出调度循环线程（2026-10-29）。

旧实现把 `ensure_dividend_data` / 日历延长 / `refresh_outcomes` 直接跑在主循环
线程上，期间 `self._stop.wait(30)` 拿不到执行权：实时轮询停摆、当天 15:30 流水线
不会被判定、`systemctl stop` 会被拖到 SIGKILL 打断数据库写入。
现在到点判定留在循环线程，执行交给 daemon 线程（对齐 `_run_review` 的既有模式）。
"""

from datetime import date, datetime, time, timedelta

import pytest

from src.models import database as db_mod


def _saturday_at(hh=9, mm=5, start=date(2026, 10, 26)):
    """推导一个周六（不写死星期，避免日历算错造成假绿）。"""
    d = start
    while d.weekday() != 5:
        d += timedelta(days=1)
    return datetime.combine(d, time(hh, mm))


@pytest.fixture
def sched(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    import src.scheduler as s
    return s, s.MarketScheduler()


@pytest.fixture
def weekly_probe(sched, monkeypatch):
    """三步维护体换成"记名字"的假件；Thread 被拦下不真起线程。"""
    s, m = sched
    called = []
    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), **kw):
            self.target, self.args = target, args
            self.daemon = kw.get("daemon")
            self.name = kw.get("name")

        def start(self):
            started.append(self)

    import src.collector.dividend_fetcher as df
    from src.collector import trading_calendar as fpc
    import src.outcome_service as osvc
    import src.config as cmod
    monkeypatch.setattr(s.threading, "Thread", FakeThread)
    monkeypatch.setattr(df, "ensure_dividend_data",
                        lambda cfg=None: called.append("dividend"))
    monkeypatch.setattr(fpc, "backfill_trading_calendar",
                        lambda: (called.append("calendar"), {"future": 1})[1])
    monkeypatch.setattr(osvc, "refresh_benchmarks",
                        lambda cfg=None: called.append("bench"))
    monkeypatch.setattr(osvc, "refresh_outcomes",
                        lambda cfg=None: called.append("outcome"))
    monkeypatch.setattr(cmod, "load_config", lambda: {"outcomes": {
        "refresh_weekday": 5, "refresh_time": "09:00", "enabled": True}})
    monkeypatch.setattr(s, "now_cn", lambda: _saturday_at())
    m._last_weekly_refresh = None
    return s, m, called, started


def test_weekly_refresh_defers_body_to_a_thread(weekly_probe):
    """核心断言：到点时维护体**不得**在调度线程里同步执行。"""
    s, m, called, started = weekly_probe
    m._check_weekly_data_refresh()
    assert called == [], "三步维护被同步执行 = 调度线程冻结数小时"
    assert len(started) == 1, "应恰好起一个后台线程"
    assert started[0].daemon is True and started[0].name == "weekly-refresh"
    assert m._weekly_in_progress is True, "进行中闸必须立刻置位，防 30 秒洪水"


def test_thread_body_still_runs_all_three_steps(weekly_probe):
    """后台体照旧执行全部三步，并在 finally 里清掉进行中标志。"""
    s, m, called, started = weekly_probe
    m._check_weekly_data_refresh()
    # 用 == 不用 is：每次取绑定方法都会新建对象
    assert started and started[0].target == m._run_weekly_refresh
    started[0].target(*started[0].args)
    assert called == ["dividend", "calendar", "bench", "outcome"]
    assert m._weekly_in_progress is False


def test_thread_body_failure_clears_flag(weekly_probe, monkeypatch):
    """后台体整体抛异常时进行中标志不能卡在 True，否则此后永不维护。"""
    s, m, called, started = weekly_probe
    import src.collector.dividend_fetcher as df
    monkeypatch.setattr(df, "ensure_dividend_data",
                        lambda cfg=None: (_ for _ in ()).throw(RuntimeError("炸")))
    m._check_weekly_data_refresh()
    started[0].target(*started[0].args)
    assert m._weekly_in_progress is False


def test_not_due_does_not_spawn_thread(weekly_probe, monkeypatch):
    """没到点不起线程、不置闸。"""
    s, m, called, started = weekly_probe
    monkeypatch.setattr(s, "now_cn", lambda: _saturday_at(hh=8, mm=0))
    m._check_weekly_data_refresh()
    assert started == [] and called == []
    assert m._weekly_in_progress is False


def test_in_progress_guard_blocks_second_spawn(weekly_probe):
    """上一轮还在跑时，同一到点窗口不得再起第二个线程。"""
    s, m, called, started = weekly_probe
    m._weekly_in_progress = True
    m._check_weekly_data_refresh()
    assert started == [] and m._last_weekly_refresh is None
