"""T15 batch1: agent/loop.py 状态机对抗（cap 边界 / dry-run / 计划消费）。"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent import AgentConfig, AgentTrader  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def mkbar(sym, day, close):
    return Bar(symbol=sym, timestamp=datetime(2024, 1, 1) + timedelta(days=day),
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def _bridge(tmp, bars):
    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                        secrets_path=str(Path(tmp) / "s.json"))
    b.data_db.upsert_bars(bars)
    b.llm_save("custom", "https://x/v1", "m", "k")
    return b


def _fresh_bars(n=30, base=10.0):
    return [mkbar("600519", 10_000 + i, base + i * 0.1) for i in range(n)]


def test_cap_boundary_executes_exactly_n(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov
    plan = {"actions": [
        {"action": "buy", "symbol": "600519", "volume": 100, "reason": "a"},
        {"action": "buy", "symbol": "600519", "volume": 100, "reason": "b"},
    ], "summary": "两单"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=2, max_order_value=20000.0))
    res = t.run(["600519"])
    filled = [d for d in res["decisions"] if d["status"].startswith("已成交")]
    assert len(filled) == 2


def test_cap_boundary_third_rejected(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov
    plan = {"actions": [
        {"action": "buy", "symbol": "600519", "volume": 100, "reason": "a"},
        {"action": "buy", "symbol": "600519", "volume": 100, "reason": "b"},
        {"action": "buy", "symbol": "600519", "volume": 100, "reason": "c"},
    ], "summary": "三单"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=2, max_order_value=20000.0))
    res = t.run(["600519"])
    assert any("超日内笔数上限" in d["status"] for d in res["decisions"])
    assert b.broker.get_position("600519").total_volume == 200


def test_dry_run_no_ledger_write(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov
    plan = {"actions": [{"action": "buy", "symbol": "600519",
                         "volume": 100, "reason": "试"}], "summary": "试"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0))
    res = t.run(["600519"], dry_run=True)
    assert res["ok"]
    assert b.broker.get_position("600519") is None
    assert b.broker.get_order_history(5) == []
