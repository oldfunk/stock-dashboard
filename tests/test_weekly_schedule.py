"""每周数据维护的星期/时刻判定单测（2026-10-21）。"""

from datetime import date, datetime, time as dtime

from src.scheduler import _parse_hhmm, _review_due


def test_parse_hhmm():
    assert _parse_hhmm("09:00") == dtime(9, 0)
    assert _parse_hhmm("23:59") == dtime(23, 59)
    assert _parse_hhmm("bad") is None


def test_saturday_due_only_after_time():
    # 2026-10-24 是周六
    assert _review_due(datetime(2026, 10, 24, 9, 0), {5}, dtime(9, 0), None)
    assert not _review_due(datetime(2026, 10, 24, 8, 59), {5}, dtime(9, 0), None)


def test_not_due_on_other_weekday_or_same_day_twice():
    assert not _review_due(datetime(2026, 10, 23, 10, 0), {5}, dtime(9, 0), None)
    assert not _review_due(datetime(2026, 10, 24, 10, 0), {5}, dtime(9, 0),
                           date(2026, 10, 24))
