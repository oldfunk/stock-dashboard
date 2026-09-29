"""复盘节奏可配（ai_review.days/time）：纯函数单测，无 DB/网络。"""
from datetime import datetime, time as dtime

from src.scheduler import (
    _parse_review_days,
    _parse_review_time,
    _review_due,
)


def _dt(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm)


class TestParseDays:
    def test_default_saturday(self):
        assert _parse_review_days(["sat"]) == {5}

    def test_multi_days(self):
        assert _parse_review_days(["mon", "wed", "fri"]) == {0, 2, 4}

    def test_daily_shortcut(self):
        assert _parse_review_days(["daily"]) == set(range(7))

    def test_int_and_scalar(self):
        assert _parse_review_days([5]) == {5}
        assert _parse_review_days("sat") == {5}

    def test_garbage_falls_back_saturday(self):
        assert _parse_review_days(["funday"]) == {5}
        assert _parse_review_days([]) == {5}
        assert _parse_review_days(None) == {5}


class TestParseTime:
    def test_normal(self):
        assert _parse_review_time("16:00") == dtime(16, 0)

    def test_invalid_falls_back_midnight(self):
        assert _parse_review_time("25:00") == dtime(0, 0)
        assert _parse_review_time("abc") == dtime(0, 0)
        assert _parse_review_time(None) == dtime(0, 0)


class TestDue:
    def test_saturday_morning_due(self):
        sat = _dt(2026, 7, 25, 0, 5)  # 周六
        assert _review_due(sat, {5}, dtime(0, 0), None) is True

    def test_wrong_weekday_not_due(self):
        fri = _dt(2026, 7, 24, 18, 0)  # 周五
        assert _review_due(fri, {5}, dtime(0, 0), None) is False

    def test_before_time_not_due(self):
        fri = _dt(2026, 7, 24, 15, 59)
        assert _review_due(fri, {4}, dtime(16, 0), None) is False
        fri2 = _dt(2026, 7, 24, 16, 0)
        assert _review_due(fri2, {4}, dtime(16, 0), None) is True

    def test_already_ran_today_not_due(self):
        sat = _dt(2026, 7, 25, 12, 0)
        assert _review_due(sat, {5}, dtime(0, 0), sat.date()) is False

    def test_daily_runs_each_day_once(self):
        for day in (27, 28, 29):  # 2026-07-27/28/29 周一/二/三
            now = _dt(2026, 7, day, 16, 30)
            assert _review_due(now, set(range(7)), dtime(16, 0), None) is True
