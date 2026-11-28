"""数据质量诊断纯函数。"""
from __future__ import annotations

import pytest

from src.data_quality import (daily_gaps, missing_months, month_range,
                              months_between, ratio)


def test_month_range_and_missing():
    assert month_range("2026-10", "2027-02") == [
        "2026-10", "2026-11", "2026-12", "2027-01", "2027-02"]
    assert missing_months({"2026-10", "2026-12"}, "2026-10", "2026-12") == [
        "2026-11"]


def test_months_between_and_ratio():
    assert months_between(["2026-09", "2026-11", "2026-10"]) == (
        "2026-09", "2026-11")
    assert ratio(1, 2) == pytest.approx(0.5)
    assert ratio(1, 0) is None


def test_daily_gaps_threshold():
    gaps = daily_gaps(["2026-11-26", "2026-11-27"],
                      {"2026-11-26": 5527, "2026-11-27": 5000}, 5527)
    assert [g["date"] for g in gaps] == ["2026-11-27"]
    assert gaps[0]["ratio"] == pytest.approx(5000 / 5527)
