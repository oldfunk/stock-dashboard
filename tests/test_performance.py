"""统一绩效（E3）纯函数：对齐与信号汇总。"""
from __future__ import annotations

import pytest

from src.performance import aligned, paper_series, signal_summary


def test_aligned_daily_and_cumulative_excess():
    paper = [("2026-11-25", 100.0), ("2026-11-26", 102.0),
             ("2026-11-27", 101.0)]
    bench = [("2026-11-25", 200.0), ("2026-11-26", 202.0),
             ("2026-11-27", 201.0)]
    rows = aligned(paper, bench)
    assert len(rows) == 3
    assert rows[0]["paper_ret"] is None
    assert rows[1]["paper_ret"] == pytest.approx(0.02)
    assert rows[1]["excess"] == pytest.approx(0.01)
    assert rows[2]["paper_cum"] == pytest.approx(0.01)
    assert rows[2]["bench_cum"] == pytest.approx(0.005)
    assert rows[2]["excess_cum"] == pytest.approx(0.005)


def test_signal_summary_ignores_null_windows():
    rows = [
        {"signal": "BUY", "ret_60": 10.0, "excess_60": 5.0, "ret_20": 2.0},
        {"signal": "BUY", "ret_60": None, "excess_60": None, "ret_20": None},
        {"signal": "AVOID", "ret_60": -4.0, "excess_60": -2.0},
    ]
    out = {x["signal"]: x for x in signal_summary(rows)}
    assert out["BUY"]["n"] == 2 and out["BUY"]["n_60"] == 1
    assert out["BUY"]["ret_60"] == pytest.approx(10.0)
    assert out["BUY"]["excess_60"] == pytest.approx(5.0)
    assert out["AVOID"]["ret_60"] == pytest.approx(-4.0)
    assert out["AVOID"]["n_20"] == 0


def test_paper_series_sorts_and_drops_invalid():
    rows = [{"timestamp": "2026-11-27T15:00:00", "total_value": 101.0},
            {"timestamp": "2026-11-25T15:00:00", "total_value": 100.0},
            {"timestamp": "", "total_value": 99.0},
            {"timestamp": "2026-11-26T15:00:00", "total_value": None}]
    assert paper_series(rows) == [("2026-11-25", 100.0),
                                  ("2026-11-27", 101.0)]
