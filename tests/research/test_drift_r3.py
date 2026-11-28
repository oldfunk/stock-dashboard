"""R3-5：滚动超额/回撤/暴露信号。"""
from __future__ import annotations

import pytest

from src.research.drift import drift_signals, rolling_excess


def test_rolling_excess_uses_latest_window():
    rows = [{"excess_return": -0.02}, {"excess_return": 0.01},
            {"excess_return": -0.01}]
    assert rolling_excess(rows, 2) == pytest.approx(-0.005)
    assert rolling_excess([], 2) is None


def test_drift_signals_negative_excess_drawdown_exposure():
    rows = [{"excess_return": -0.02}, {"excess_return": -0.01}]
    panel = {"performance": {"current_drawdown": 0.12, "exposure": 0.85}}
    sig = drift_signals(rows, panel, window=2)
    kinds = {s["type"] for s in sig}
    assert kinds == {"negative_excess", "drawdown", "over_exposure"}


def test_drift_no_signal_when_healthy():
    rows = [{"excess_return": 0.01}]
    panel = {"performance": {"current_drawdown": 0.02, "exposure": 0.2}}
    assert drift_signals(rows, panel, window=2) == []
