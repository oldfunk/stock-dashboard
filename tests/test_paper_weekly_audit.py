"""周度审计（B3）纯函数：计划/成交匹配、费用、换手、渲染。"""
from __future__ import annotations

import json

import pytest

from scripts.paper_weekly_audit import plan_stats, render, summarize


def _plan(pid, day, actions, rejected=None, status="done"):
    return {"id": pid, "plan_date": day, "status": status,
            "plan_json": json.dumps({"actions": actions,
                                     "actions_rejected": rejected or []},
                                    ensure_ascii=False)}


def test_plan_stats_matches_same_day_direction():
    plans = [_plan(1, "2026-11-27", [
        {"action": "buy", "symbol": "000848", "volume": 100},
        {"action": "hold", "symbol": "000792", "volume": 0}])]
    fills = [{"symbol": "000848", "direction": 1,
              "timestamp": "2026-11-27T09:30:34", "price": 7.4,
              "volume": 100, "commission": 5.0}]
    st = plan_stats(plans, fills)
    assert st["planned"] == {"buy": 1, "sell": 0, "hold": 1}
    assert st["matched"] == 1
    assert st["compliance"] == 1.0


def test_plan_stats_counts_rejected_and_unmatched():
    plans = [_plan(2, "2026-11-24", [
        {"action": "sell", "symbol": "000426", "volume": 100}],
        rejected=[{"symbol": "601899", "action": "buy", "reason": "超买入区间"}])]
    st = plan_stats(plans, [])
    assert st["matched"] == 0 and st["compliance"] == 0.0
    assert st["rejected"][0]["reason"] == "超买入区间"


def test_summarize_fees_turnover_nav():
    plans = [_plan(1, "2026-11-27", [
        {"action": "buy", "symbol": "000848", "volume": 100}])]
    fills = [{"symbol": "000848", "direction": 1,
              "timestamp": "2026-11-27T09:30:34", "price": 7.4,
              "volume": 100, "commission": 5.0, "stamp_duty": 0.0,
              "transfer_fee": 0.0074}]
    navs = [{"timestamp": "2026-11-20T15:00:00", "total_value": 100000.0},
            {"timestamp": "2026-11-27T15:00:00", "total_value": 101000.0}]
    s = summarize(plans, fills, navs)
    assert s["fills"] == 1 and s["buy_fills"] == 1
    assert s["fees"] == pytest.approx(5.0074)
    assert s["turnover"] == pytest.approx(740.0)
    assert s["turnover_pct"] == pytest.approx(740.0 / 100500.0)
    assert s["nav_pct"] == pytest.approx(0.01)
    text = render(s, "2026-11-20", "2026-11-27")
    assert "买卖兑现率" in text and "NAV" in text
