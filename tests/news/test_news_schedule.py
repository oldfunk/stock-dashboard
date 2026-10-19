"""新闻舆情调度门禁（2026-10-19）：到点触发、当日幂等、时刻解析。

hermetic：monkeypatch now_cn 与线程启动，不真跑 LLM/抓取。
"""
from datetime import datetime

import pytest


@pytest.fixture
def sch(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    import src.scheduler as sch
    return sch


def _at(sch, y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm)


def test_parse_hhmm_valid_and_invalid(sch):
    from datetime import time as dtime
    assert sch._parse_hhmm("09:05") == dtime(9, 5)
    assert sch._parse_hhmm("18:30") == dtime(18, 30)
    assert sch._parse_hhmm("bad") is None
    assert sch._parse_hhmm("25:00") is None
    assert sch._parse_hhmm(None) is None


def test_news_jobs_triggers_once_per_day(sch, monkeypatch):
    """到点各跑一次；当日不再跑（幂等）。"""
    fired = []
    monkeypatch.setattr(sch.MarketScheduler, "_run_news_ingest",
                        staticmethod(lambda: fired.append("ingest")))
    monkeypatch.setattr(sch.MarketScheduler, "_run_news_summary",
                        staticmethod(lambda: fired.append("summary")))
    import src.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config", lambda: {"news": {
        "poll_interval_min": 15, "active_hours": [7, 23], "summary_time": "18:30"}}, raising=False)

    s = sch.MarketScheduler()
    calls = []
    class FakeThread:
        def __init__(self, target=None, **kw):
            self.target = target
        def start(self):
            calls.append(self.target)
    monkeypatch.setattr(sch.threading, "Thread", FakeThread)

    monkeypatch.setattr(sch, "now_cn", lambda: _at(sch, 2026, 10, 19, 9, 6))
    s._check_news_jobs()
    assert len(calls) == 1, "09:05 的抓取应触发一次"

    # 同一天再轮询：不再触发
    calls.clear()
    s._check_news_jobs()
    assert calls == [], "当日幂等，不能重复抓"

    # 换一天：摘要时刻也过点后才跑
    s._last_news_ingest = {}
    s._last_news_summary = None
    calls.clear()
    monkeypatch.setattr(sch, "now_cn", lambda: _at(sch, 2026, 10, 20, 18, 31))
    s._check_news_jobs()
    assert len(calls) == 2, "抓取 + 摘要各一次"


def test_news_jobs_respects_active_window(sch, monkeypatch):
    """活跃时段外不轮询（不打扰数据源）；间隔未到不重复触发。"""
    monkeypatch.setattr(sch.MarketScheduler, "_run_news_ingest",
                        staticmethod(lambda: None))
    monkeypatch.setattr(sch.MarketScheduler, "_run_news_summary",
                        staticmethod(lambda: None))
    import src.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config", lambda: {"news": {
        "poll_interval_min": 15, "active_hours": [7, 23],
        "summary_time": "18:30"}}, raising=False)
    calls = []

    class FakeThread:
        def __init__(self, target=None, **kw):
            calls.append(target)

        def start(self):
            pass

    monkeypatch.setattr(sch.threading, "Thread", FakeThread)
    s = sch.MarketScheduler()
    monkeypatch.setattr(sch, "now_cn", lambda: _at(sch, 2026, 10, 19, 3, 0))
    s._check_news_jobs()
    assert calls == [], "活跃时段外不该轮询"
    calls.clear()
    s._last_news_ingest_at = None
    monkeypatch.setattr(sch, "now_cn", lambda: _at(sch, 2026, 10, 19, 10, 0))
    s._check_news_jobs()
    assert len(calls) == 1, "窗口内首轮应触发"
    calls.clear()
    monkeypatch.setattr(sch, "now_cn", lambda: _at(sch, 2026, 10, 19, 10, 5))
    s._check_news_jobs()
    assert calls == [], "间隔未到不重复触发"
    monkeypatch.setattr(sch, "now_cn", lambda: _at(sch, 2026, 10, 19, 10, 16))
    s._check_news_jobs()
    assert len(calls) == 1, "超过间隔应再触发"

def test_news_jobs_disabled(sch, monkeypatch):
    import src.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config",
                        lambda: {"news": {"enabled": False}}, raising=False)
    calls = []

    class FakeThread:
        def __init__(self, target=None, **kw):
            calls.append(target)

        def start(self):
            pass

    monkeypatch.setattr(sch.threading, "Thread", FakeThread)
    monkeypatch.setattr(sch, "now_cn", lambda: _at(sch, 2026, 10, 19, 23, 0))
    sch.MarketScheduler()._check_news_jobs()
    assert calls == []
