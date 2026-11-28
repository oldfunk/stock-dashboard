"""纸盘绩效（B2）纯函数：对齐、逐日/累计超额、渲染（不读生产库）。"""
from __future__ import annotations

import pytest

from scripts.paper_performance import aligned, paper_series, render


def test_paper_series_sorts_and_drops_invalid():
    rows = [{"timestamp": "2026-11-27T15:00:00", "total_value": 101.0},
            {"timestamp": "2026-11-25T15:00:00", "total_value": 100.0},
            {"timestamp": "", "total_value": 99.0},
            {"timestamp": "2026-11-26T15:00:00", "total_value": None}]
    assert paper_series(rows) == [("2026-11-25", 100.0),
                                  ("2026-11-27", 101.0)]


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
    assert rows[2]["excess_cum"] == pytest.approx(0.01)
    assert "超额" in render(rows, "sh000300")


def test_aligned_requires_two_common_dates():
    paper = [("2026-11-25", 100.0)]
    bench = [("2026-11-25", 200.0)]
    assert aligned(paper, bench) == []
    assert "无可对齐" in render(aligned(paper, bench), "sh000300")
