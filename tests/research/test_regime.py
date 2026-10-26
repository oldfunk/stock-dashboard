"""Regime 规则回归测试（R1-3）。"""

import pytest

from src.research.market_state import MarketStateVector
from src.research.regime import (
    RegimeError, RegimeRule, classify_regime, default_rules, evaluate_rule,
)


def _vector(metrics):
    return MarketStateVector(trade_date="2026-10-26", metrics=metrics)


def test_default_rules_cover_four_blocks():
    rules = default_rules()
    assert len(rules) == 4
    assert {r.name for r in rules} == {"trend", "valuation", "volatility", "style"}


def test_evaluate_rule_boundaries():
    rule = RegimeRule("trend", "breadth", "pct_above_ma60", 0.6, 0.4,
                      direction="higher_better")
    assert evaluate_rule(rule, 0.7) == 1.0
    assert evaluate_rule(rule, 0.3) == -1.0
    assert evaluate_rule(rule, 0.5) == 0.0
    assert evaluate_rule(rule, None) is None


def test_classify_risk_on_when_all_agree():
    result = classify_regime(_vector({
        "pct_above_ma60": 0.8, "pe_percentile_5y": 0.1,
        "volatility_20d": 0.10, "value_vs_growth": 0.05}))
    assert result.regime == "risk_on"
    assert result.confidence == 1.0
    assert len(result.drivers) == 4
    assert result.missing_fields == []


def test_classify_risk_off_when_all_agree():
    result = classify_regime(_vector({
        "pct_above_ma60": 0.2, "pe_percentile_5y": 0.9,
        "volatility_20d": 0.40, "value_vs_growth": -0.05}))
    assert result.regime == "risk_off"
    assert result.confidence == 1.0


def test_classify_neutral_on_mixed_signals():
    result = classify_regime(_vector({
        "pct_above_ma60": 0.8, "pe_percentile_5y": 0.9,
        "volatility_20d": 0.10, "value_vs_growth": -0.05}))
    assert result.regime == "neutral"
    assert result.confidence == 0.0


def test_missing_fields_are_not_guessed():
    result = classify_regime(_vector({"pct_above_ma60": 0.8}))
    # 仅 1 条规则有数据 -> 证据不足，不给结论（不猜）
    assert result.regime == "unknown"
    assert result.confidence == 0.0
    assert set(result.missing_fields) == {
        "pe_percentile_5y", "volatility_20d", "value_vs_growth"}
    assert len(result.drivers) == 1


def test_two_rules_suffice():
    result = classify_regime(_vector({
        "pct_above_ma60": 0.8, "pe_percentile_5y": 0.1}))
    assert result.regime == "risk_on"
    assert result.confidence == 1.0


def test_all_missing_is_unknown():
    result = classify_regime(_vector({}))
    assert result.regime == "unknown"
    assert result.confidence == 0.0
    assert len(result.missing_fields) == 4


def test_rule_validation_rejects_bad_direction():
    with pytest.raises(RegimeError):
        RegimeRule("x", "b", "f", 1.0, 0.0, direction="sideways")
