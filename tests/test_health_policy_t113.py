"""T113：health_policy（off/advisory/enforce）与买入闸门。"""
from datetime import datetime, timedelta

import pytest

import paper_trading.cli as hb
from paper_trading.agent.loop import AgentConfig, AgentTrader
from paper_trading.models import Bar


def _bars(n=30, base=10.0):
    return [Bar(symbol="600519",
                timestamp=datetime(2024, 1, 1) + timedelta(days=i),
                open=base + i * 0.1, high=base + i * 0.1 + 1,
                low=base + i * 0.1 - 1, close=base + i * 0.1, volume=100)
            for i in range(n)]


def _bridge(tmp_path):
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.data_db.upsert_bars(_bars())
    return b


def test_health_policy_validation():
    assert AgentConfig().health_policy == "advisory"
    with pytest.raises(ValueError):
        AgentConfig(health_policy="sometimes")


def test_apply_health_policy_off_and_enforce():
    t = AgentTrader.__new__(AgentTrader)
    t.cfg = AgentConfig(health_policy="off")
    ctx = {"strategy_health": {"health": "demote"}}
    assert "strategy_health" not in t._apply_health_policy(ctx)
    t.cfg = AgentConfig(health_policy="enforce")
    ctx = {"strategy_health": {"health": "demote"}}
    out = t._apply_health_policy(ctx)["strategy_health"]
    assert out["enforce"] is True and "买入" in out["note"]


def test_enforce_blocks_buy_but_not_hold(monkeypatch, tmp_path):
    b = _bridge(tmp_path)
    t = AgentTrader(b, AgentConfig(health_policy="enforce",
                                   max_orders_per_run=3,
                                   max_order_value=20000.0))
    monkeypatch.setattr(AgentTrader, "_health_block",
                        lambda self: {"health": "demote"})
    res = t._execute_actions(
        actions=[
            {"action": "buy", "symbol": "600519", "volume": 100,
             "reason": "买", "basis": ["recent_closes"], "risk_stop": 8.0},
            {"action": "hold", "symbol": "600519", "volume": 0,
             "reason": "持"},
        ],
        summary="", symbols=["600519"], all_bars={"600519": _bars()},
        latest_prices={"600519": 12.9}, cash=1000000.0, positions={},
        nav_total=1000000.0, dry_run=True, mode="live",
        now=datetime.now().isoformat(), from_plan=True, plan_id=1)
    buy, hold = res["decisions"]
    assert "策略健康" in buy["status"] and "enforce" in buy["status"]
    assert hold["status"] == "持有不动"


def test_advisory_does_not_block_buy(monkeypatch, tmp_path):
    b = _bridge(tmp_path)
    t = AgentTrader(b, AgentConfig(health_policy="advisory",
                                   max_orders_per_run=3,
                                   max_order_value=20000.0))
    res = t._execute_actions(
        actions=[{"action": "buy", "symbol": "600519", "volume": 100,
                  "reason": "买", "basis": ["recent_closes"], "risk_stop": 8.0}],
        summary="", symbols=["600519"], all_bars={"600519": _bars()},
        latest_prices={"600519": 12.9}, cash=1000000.0, positions={},
        nav_total=1000000.0, dry_run=True, mode="live",
        now=datetime.now().isoformat(), from_plan=True, plan_id=1)
    assert "策略健康" not in res["decisions"][0]["status"]


def test_enforce_rejects_buy_condition(monkeypatch, tmp_path):
    b = _bridge(tmp_path)
    t = AgentTrader(b, AgentConfig(health_policy="enforce"))
    monkeypatch.setattr(AgentTrader, "_health_block",
                        lambda self: {"health": "demote"})
    exp = (datetime.now() + timedelta(days=2)).date().isoformat()
    res = t._arm_conditions([{"symbol": "600519", "side": "buy",
                              "cond_type": "price_below", "cond_value": 10.0,
                              "volume": 100, "value": 0,
                              "expiry": f"{exp}T23:59", "note": "x",
                              "source": "ai"}])
    assert res["n_created"] == 0 and res["n_rejected"] == 1
    assert "策略健康" in res["rejected"][0]["why"]
