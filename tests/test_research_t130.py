"""T130：参数研究流程（样本内→样本外→前向占位，不自动写生产）。"""
import math
from datetime import datetime, timedelta
from pathlib import Path

import paper_trading.cli as hb
from paper_trading.models import Bar
from paper_trading.strategy.research import classify, run_research


def _bridge(tmp_path):
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    bars = [Bar(symbol="600519",
                timestamp=datetime(2025, 1, 1) + timedelta(days=i),
                open=20 + 4 * math.sin(i / 7) + i * 0.02,
                high=21 + 4 * math.sin(i / 7) + i * 0.02,
                low=19 + 4 * math.sin(i / 7) + i * 0.02,
                close=20 + 4 * math.sin(i / 7) + i * 0.02,
                volume=100000) for i in range(160)]
    b.data_db.upsert_bars(bars)
    return b


def test_classify_reject_and_candidate():
    assert classify({"error": "x"})["decision"] == "reject"
    assert classify({"in_best_oos_excess_pct": -1.0})["decision"] == "reject"
    assert classify({"in_best_oos_excess_pct": 2.0, "out_of_sample": [
        {"oos_trades": 10}], "n_oos_positive": 3, "n_tested": 3,
        "overfit_gap_pct": 1.0})["decision"] == "candidate"
    assert classify({"in_best_oos_excess_pct": 2.0, "out_of_sample": [
        {"oos_trades": 10}], "n_oos_positive": 3, "n_tested": 3,
        "overfit_gap_pct": 8.0})["decision"] == "overfit_risk"


def test_run_research_returns_guardrails(tmp_path):
    b = _bridge(tmp_path)
    out = run_research(b, grid="ma_cross", symbols=["600519"], days=100,
                       cash=100000, top=3, split=0.6)
    assert out["ok"] is True
    data = out
    r = data["research"]
    assert r["auto_apply"] is False
    assert r["forward"]["status"] == "pending"
    assert r["grid"] == "ma_cross" and r["days"] <= 100
    assert data["split"]["in_days"] + data["split"]["oos_days"] <= 100
    assert data["data_fingerprint"] or data.get("fingerprint")
    assert "decision" in r


def test_run_research_unknown_grid(tmp_path):
    b = _bridge(tmp_path)
    out = run_research(b, grid="nope", symbols=["600519"])
    assert out["ok"] is False and "未知网格" in out["error"]
