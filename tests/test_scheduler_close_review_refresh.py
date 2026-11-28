"""收盘检阅 20:55 刷新：面板 asof 到当天才重跑，且不覆盖人工批注。"""
from __future__ import annotations

from datetime import date, datetime

from src.scheduler import MarketScheduler


class _FakeThread:
    started = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        type(self).started.append((self.target, self.args))


def _sched():
    s = MarketScheduler.__new__(MarketScheduler)
    s._last_close_review = None
    s._close_review_attempts = {}
    s._close_review_refresh_done = None
    s._is_trading_day = lambda d: True
    return s


def _cfg():
    return {"review_enabled": True, "review_time": "15:45",
            "review_retry_time": "18:30", "review_refresh_time": "20:55"}


def test_refresh_runs_when_panel_asof_is_today(monkeypatch):
    from src.models import news_review as nr
    import src.paper_bridge as pb
    import src.scheduler as sched_mod

    class FakeDAO:
        def get_review(self, d):
            return {"status": "published"}

    monkeypatch.setattr(nr, "NewsReviewDAO", FakeDAO)
    monkeypatch.setattr(pb, "load_panel_snapshot", lambda **k: {
        "available": True, "asof": "2026-11-30"})
    monkeypatch.setattr(sched_mod.threading, "Thread", _FakeThread)
    _FakeThread.started = []
    s = _sched()
    s._check_close_review(_cfg(), datetime(2026, 11, 30, 20, 56))
    assert len(_FakeThread.started) == 1
    assert s._close_review_refresh_done == date(2026, 11, 30)


def test_no_refresh_when_panel_still_previous_day(monkeypatch):
    from src.models import news_review as nr
    import src.paper_bridge as pb
    import src.scheduler as sched_mod

    class FakeDAO:
        def get_review(self, d):
            return {"status": "published"}

    monkeypatch.setattr(nr, "NewsReviewDAO", FakeDAO)
    monkeypatch.setattr(pb, "load_panel_snapshot", lambda **k: {
        "available": True, "asof": "2026-11-27"})
    monkeypatch.setattr(sched_mod.threading, "Thread", _FakeThread)
    _FakeThread.started = []
    _sched()._check_close_review(_cfg(), datetime(2026, 11, 30, 20, 56))
    assert _FakeThread.started == []


def test_no_refresh_for_annotated_note(monkeypatch):
    from src.models import news_review as nr
    import src.scheduler as sched_mod

    class FakeDAO:
        def get_review(self, d):
            return {"status": "annotated"}

    monkeypatch.setattr(nr, "NewsReviewDAO", FakeDAO)
    monkeypatch.setattr(sched_mod.threading, "Thread", _FakeThread)
    _FakeThread.started = []
    _sched()._check_close_review(_cfg(), datetime(2026, 11, 30, 20, 56))
    assert _FakeThread.started == []
