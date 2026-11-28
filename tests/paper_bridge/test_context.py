"""E4 组合上下文：面板优先、桥回退、缓存。"""
from __future__ import annotations

from src.paper_bridge import context


def test_build_context_panel():
    panel = {"available": True, "asof": "2026-11-27",
             "performance": {"total_return": -0.0287, "today_pnl_pct": 0.0084,
                             "realized_pnl": -2859.54, "fees_total": 40.68,
                             "mdd": 0.0417, "exposure": 0.1516},
             "positions": [{"symbol": "000792", "total_volume": 400,
                            "avg_cost": 23.98, "current_price": 22.64}]}
    text = context.build_context(panel=panel,
                                 bridge={"pending_plan": {"id": 24,
                                                          "plan_date": "2026-11-30"}})
    assert "asof 2026-11-27" in text
    assert "-2.87%" in text and "000792" in text
    assert "#24" in text and "不得据此给交易建议" in text


def test_build_context_empty_when_no_data():
    assert context.build_context(panel={}, bridge={}) == ""


def test_portfolio_context_uses_cache(monkeypatch):
    calls = []
    import src.paper_bridge as pb

    def fake_panel(**k):
        calls.append(1)
        return {"available": True, "asof": "2026-11-27",
                "performance": {"total_return": -0.01}, "positions": []}

    monkeypatch.setattr(pb, "load_panel_snapshot", fake_panel)
    monkeypatch.setattr(pb, "load_status", lambda **k: {
        "nav": {"data": [{"timestamp": "2026-11-27"}]},
        "plan": {"data": []}, "positions": {"data": []}})
    context.clear_cache()
    a = context.portfolio_context()
    b = context.portfolio_context()
    assert a == b and len(calls) == 1
    context.clear_cache()
