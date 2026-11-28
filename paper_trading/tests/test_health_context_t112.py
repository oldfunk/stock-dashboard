"""T112：策略健康度进入 AI 上下文（advisory，不拦买）。"""
import tempfile
from pathlib import Path

import paper_trading.cli as hb
from paper_trading.agent.prompts import TRADER_SYSTEM


def _bridge(tmp_path):
    return hb.TradingBridge(
        data_db=str(tmp_path / "data.db"),
        account_db=str(tmp_path / "acct.db"),
        config_path="/nonexistent.yaml",
        secrets_path=str(tmp_path / "s.json"))


def test_context_includes_strategy_health(monkeypatch, tmp_path):
    sample = {"available": True, "health": "demote",
              "recommendation": "observe",
              "reasons": ["20日超额 -5.14% ≤ -5%"],
              "window": {"start": "2026-09-28", "end": "2026-11-27", "days": 32},
              "metrics": {"excess": 0.034, "excess_20": -0.0514,
                          "max_drawdown": 0.0913, "turnover": 3.67,
                          "fee_drag": 0.0032},
              "stale": False}
    monkeypatch.setattr("paper_trading.strategy.health.health_payload",
                        lambda *a, **k: sample)
    ctx = _bridge(tmp_path).llm_context()
    sh = ctx["strategy_health"]
    assert sh["health"] == "demote" and sh["recommendation"] == "observe"
    assert sh["metrics"]["excess_20"] == -0.0514
    assert "不是命令" in sh["note"]
    assert "advisory" in ctx["legend"]["strategy_health"]
    assert "MA20/60" in ctx["note"]


def test_context_survives_health_failure(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("health down")

    monkeypatch.setattr("paper_trading.strategy.health.health_payload", boom)
    ctx = _bridge(tmp_path).llm_context()
    assert ctx["strategy_health"]["available"] is False
    assert "RuntimeError" in ctx["strategy_health"]["reason"]


def test_prompt_marks_health_as_advisory():
    assert "策略健康" in TRADER_SYSTEM
    assert "不会因 demote 自动拦买" in TRADER_SYSTEM
