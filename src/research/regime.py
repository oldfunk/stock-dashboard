"""Regime 规则（2026-10-27，R1-3）。

透明规则（非黑箱）：趋势 / 估值 / 波动 / 风格，每条规则是**显式阈值**，
输出 ``regime / confidence / drivers``。

- 规则字段缺失 -> 该规则不参与评分（不猜、不补）。
- ``confidence`` = |信号和| / 已评估规则数（全票一致 = 1.0）。
- 不引入任何模型/黑箱：全部规则可审计。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Iterable

from src.research.market_state import MarketStateVector


class RegimeError(ValueError):
    """规则非法。"""


MIN_RULES = 2   # 至少 2 条规则有数据才给结论


@dataclass
class RegimeRule:
    name: str
    block: str
    field: str
    risk_on_threshold: float
    risk_off_threshold: float
    direction: str = "lower_better"   # lower_better: 低于阈值=risk_on
    description: str = ""

    def __post_init__(self) -> None:
        if self.direction not in ("higher_better", "lower_better"):
            raise RegimeError(f"非法方向: {self.direction}")
        if self.risk_on_threshold == self.risk_off_threshold:
            raise RegimeError("risk_on/risk_off 阈值不可相同（无中性区间）")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeResult:
    trade_date: str
    regime: str
    confidence: float
    drivers: list[dict[str, Any]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date, "regime": self.regime,
            "confidence": self.confidence, "drivers": self.drivers,
            "missing_fields": self.missing_fields,
        }


def default_rules() -> list[RegimeRule]:
    """透明规则集（阈值可审计）。

    阈值量纲必须与 `market_state.default_fields()` 里该字段的单位声明一致：
    声明 "%" 的字段（占比、分位）生产者输出 0–100，阈值也写 0–100；
    声明 "x" 的字段（比值、小数波动率）阈值写小数。历史上 trend/valuation
    两条按小数写阈值，而生产者输出百分数，导致「0.6% 的股票站上 MA60」
    即判看多、regime 长期 risk_on（审计 N4）。
    """
    return [
        RegimeRule("trend", "breadth", "pct_above_ma60",
                   risk_on_threshold=60.0, risk_off_threshold=40.0,
                   direction="higher_better",
                   description="站上 60 日均线占比 >=60% 看多，<=40% 看空"),
        RegimeRule("valuation", "valuation", "pe_percentile_5y",
                   risk_on_threshold=20.0, risk_off_threshold=80.0,
                   direction="lower_better",
                   description="PE 5 年分位 <=20% 看多，>=80% 看空"),
        RegimeRule("volatility", "liquidity", "volatility_20d",
                   risk_on_threshold=0.15, risk_off_threshold=0.30,
                   direction="lower_better",
                   description="20 日波动率 <=15% 看多，>=30% 看空（字段单位 x，阈值为小数）"),
        # style/value_vs_growth 规则已移除（2026-10-29 母方裁定，Issue #9）：
        # 该字段从未注册进 StateSchema、无任何生产者——规则永远拿不到数据，
        # 属"宣传与实际不符"的假门（与 N5 同族教训）。将来有字段生产者再注册
        # 规则 + 同步 test_regime 的量纲对齐用例。
    ]


def evaluate_rule(rule: RegimeRule, value: float | None) -> float | None:
    """单条规则评分：+1（risk_on）/ -1（risk_off）/ 0（中性）/ None（缺数据）。"""
    if value is None:
        return None
    if rule.direction == "lower_better":
        if value <= rule.risk_on_threshold:
            return 1.0
        if value >= rule.risk_off_threshold:
            return -1.0
        return 0.0
    if value >= rule.risk_on_threshold:
        return 1.0
    if value <= rule.risk_off_threshold:
        return -1.0
    return 0.0


def classify_regime(vector: MarketStateVector,
                    rules: Iterable[RegimeRule] | None = None) -> RegimeResult:
    """按透明规则判定 regime；缺字段的规则不参与（不猜）。"""
    rules = list(rules) if rules is not None else default_rules()
    drivers: list[dict[str, Any]] = []
    missing: list[str] = []
    score = 0.0
    evaluated = 0
    for rule in rules:
        value = vector.metrics.get(rule.field)
        signal = evaluate_rule(rule, value)
        if signal is None:
            missing.append(rule.field)
            continue
        evaluated += 1
        score += signal
        drivers.append({"rule": rule.name, "block": rule.block,
                        "field": rule.field, "value": value,
                        "signal": signal})
    if evaluated == 0:
        return RegimeResult(vector.trade_date, "unknown", 0.0, drivers, missing)
    # 证据不足（<2 条规则）不给结论，避免单票高置信误导
    if evaluated < MIN_RULES:
        return RegimeResult(vector.trade_date, "unknown", 0.0, drivers, missing)
    regime = "risk_on" if score >= 1 else ("risk_off" if score <= -1 else "neutral")
    return RegimeResult(vector.trade_date, regime,
                        round(abs(score) / evaluated, 4), drivers, missing)
