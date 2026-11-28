"""T115：AI 风格审计回放（健康度输入 → 选择 → 理由）。"""
from datetime import datetime, timedelta
from pathlib import Path

import paper_trading.cli as hb
from paper_trading.models import Bar


def _bars(sym="600519", n=30):
    return [Bar(symbol=sym, timestamp=datetime(2024, 1, 1) + timedelta(days=i),
                open=10 + i * 0.1, high=11 + i * 0.1, low=9 + i * 0.1,
                close=10 + i * 0.1, volume=100) for i in range(n)]


def _bridge(tmp_path: Path):
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.data_db.upsert_bars(_bars())
    return b


def _save(b, *, summary, health, actions=None, allocations=None):
    return b.broker.save_agent_decision(
        decision_date="2026-11-27", plan_id=1, mode="live", from_plan=True,
        pool_source="config", summary=summary,
        actions=actions or [{"action": "hold", "symbol": "600519",
                             "reason": "观察", "status": "持有不动"}],
        conditions=[], result={
            "strategy_health": health,
            "strategy_allocations": allocations,
            "decisions": actions or []})


def _demote():
    return {"available": True, "health": "demote", "recommendation": "observe",
            "reasons": ["20日超额 -5.14% ≤ -5%"],
            "window": {"start": "2026-09-28", "end": "2026-11-27", "days": 32},
            "metrics": {"excess_20": -0.0514}}


def test_replay_shows_health_acknowledged(tmp_path):
    b = _bridge(tmp_path)
    _save(b, summary="健康降级，本轮放缓买入，只观察",
          health=_demote(), allocations={"enabled": False})
    out = hb._replay_decision(b, "2026-11-27", 1)
    assert out["ok"] is True
    rev = out["health_review"]
    assert rev["demote"] is True and rev["acknowledged"] is True
    assert "放缓" in rev["evidence"]
    assert rev["input"]["metrics"]["excess_20"] == -0.0514
    assert out["strategy_allocations"] == {"enabled": False}


def test_replay_flags_missing_health_explanation(tmp_path):
    b = _bridge(tmp_path)
    _save(b, summary="继续持有，等反弹", health=_demote())
    out = hb._replay_decision(b, "2026-11-27", 1)
    rev = out["health_review"]
    # “继续”是合法选择，但必须显式：这里命中了“继续”
    assert rev["acknowledged"] is True

    _save(b, summary="按计划执行", health=_demote(), actions=[
        {"action": "buy", "symbol": "600519", "reason": "趋势向上",
         "status": "已成交"}])
    out = hb._replay_decision(b, "2026-11-27", 1)
    assert out["health_review"]["acknowledged"] is False


def test_replay_non_demote_has_no_ack_requirement(tmp_path):
    b = _bridge(tmp_path)
    _save(b, summary="正常执行",
          health={"available": True, "health": "healthy",
                  "recommendation": "active", "reasons": []})
    out = hb._replay_decision(b, "2026-11-27", 1)
    rev = out["health_review"]
    assert rev["demote"] is False and rev["acknowledged"] is None


def test_replay_carries_strategy_per_action(tmp_path):
    b = _bridge(tmp_path)
    _save(b, summary="执行", health=_demote(),
          actions=[{"action": "buy", "symbol": "600519", "strategy": "ma_cross",
                    "reason": "放缓后小仓", "status": "已成交"}])
    out = hb._replay_decision(b, "2026-11-27", 1)
    assert out["comparison"][0]["strategy"] == "ma_cross"
