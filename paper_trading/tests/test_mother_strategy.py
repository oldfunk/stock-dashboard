"""母项目价值策略闸门（2026-11-19）。

守两件事：
1. `parse_price_zone` 只认价格区间，市值口径不许误判；
2. 买入闸门 fail-closed：HOLD/AVOID/过期/缺分析/超出区间一律拒绝，
   真实事故形状（601899 HOLD、区间 8-12、现价 23.88）必须被杀掉。
"""

from types import SimpleNamespace

import pytest

from paper_trading.agent.loop import AgentTrader
from paper_trading.agent.mother_strategy import (
    buy_violation,
    parse_price_zone,
    ref_text,
    refs_from_screening,
)

REFS = {
    "601899": {"signal": "HOLD", "buy_zone": "8-12", "stop_loss": "跌破7元",
               "target_price": "18-20", "score": 89.7, "ai_failed": False},
    "002558": {"signal": "BUY", "buy_zone": "18-22元", "stop_loss": "跌破15元",
               "target_price": "25", "score": 70.5, "ai_failed": False},
    "000426": {"signal": "BUY", "buy_zone": "300-320亿市值", "stop_loss": "ROE",
               "target_price": "", "score": 84.5, "ai_failed": False},
    "000792": {"signal": "HOLD", "buy_zone": "18-20元", "stop_loss": "跌破15元",
               "target_price": "", "score": 96.2, "ai_failed": False},
    "920000": {"signal": "", "buy_zone": "", "stop_loss": "",
               "target_price": "", "score": 0, "ai_failed": True},
}


# ── 区间解析 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expect", [
    ("8-12", (8.0, 12.0)),
    ("18-22元", (18.0, 22.0)),
    ("8~12", (8.0, 12.0)),
    ("低于15元", (None, 15.0)),
    ("8元以下", (None, 8.0)),
    ("≥20元", (20.0, None)),
])
def test_parse_price_zone(text, expect):
    assert parse_price_zone(text) == expect


@pytest.mark.parametrize("text", ["300-320亿市值", "", None, "合理区间", "看情况"])
def test_parse_price_zone_rejects_non_price(text):
    assert parse_price_zone(text) is None


# ── 买入闸门 ──────────────────────────────────────────────────────────────

def test_hold_signal_is_rejected():
    """真实事故：母分析 HOLD，子项目却买。"""
    why = buy_violation("601899", 23.88, REFS, asof="2026-11-19",
                        today="2026-11-19")
    assert why and "HOLD" in why


def test_avoid_signal_is_rejected():
    why = buy_violation("000792", 21.43, {"000792": {**REFS["000792"],
                                                      "signal": "AVOID"}})
    assert why and "AVOID" in why


def test_buy_outside_zone_is_rejected():
    why = buy_violation("601899", 23.88, REFS)  # 先改成 BUY 才能测区间
    assert why and "HOLD" in why
    refs = {"601899": {**REFS["601899"], "signal": "BUY"}}
    why = buy_violation("601899", 23.88, refs)
    assert why and "买入区间" in why


def test_buy_inside_zone_passes():
    assert buy_violation("002558", 18.27, REFS) is None


def test_marketcap_zone_only_checks_signal():
    """市值口径无法对价：BUY 放行，非 BUY 仍拒。"""
    assert buy_violation("000426", 19.81, REFS) is None
    refs = {"000426": {**REFS["000426"], "signal": "HOLD"}}
    assert buy_violation("000426", 19.81, refs)


def test_missing_or_failed_analysis_is_rejected():
    assert buy_violation("999999", 10.0, REFS)
    assert buy_violation("920000", 10.0, REFS)


def test_stale_analysis_is_rejected():
    why = buy_violation("002558", 18.27, REFS, asof="2026-11-10",
                        today="2026-11-19")
    assert why and "过期" in why


# ── 引用文本 ──────────────────────────────────────────────────────────────

def test_refs_from_screening_parses_json_string():
    block = {"rows": [{"code": "601899", "score": 89.7,
                       "ai_trade_strategy": '{"signal":"HOLD","buy_zone":"8-12"}',
                       "ai_failed": 0}]}
    refs = refs_from_screening(block)
    assert refs["601899"]["signal"] == "HOLD"
    assert refs["601899"]["buy_zone"] == "8-12"


def test_ref_text_marks_missing_and_failed():
    text = ref_text(REFS, ["601899", "920000", "999999"])
    assert "601899：HOLD" in text
    assert "分析缺失/失败" in text
    assert "无该标的最新分析" in text


# ── 接线：只有母方案才走闸门 ──────────────────────────────────────────────

def test_mother_block_only_for_mother_scheme():
    t = AgentTrader.__new__(AgentTrader)
    t.bridge = SimpleNamespace(scheme=SimpleNamespace(source="mother"))
    t._mother_refs = lambda: (REFS, "2026-11-19")
    assert t._mother_block("601899", 23.88)
    assert t._mother_block("002558", 18.27) is None

    t.bridge = SimpleNamespace(scheme=SimpleNamespace(source="general"))
    assert t._mother_block("601899", 23.88) is None
