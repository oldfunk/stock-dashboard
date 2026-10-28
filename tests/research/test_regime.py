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
    """引擎语义（与量纲无关）：higher/lower_better 的三段判定与缺数据不参与。"""
    rule = RegimeRule("trend", "breadth", "pct_above_ma60", 60.0, 40.0,
                      direction="higher_better")
    assert evaluate_rule(rule, 70.0) == 1.0
    assert evaluate_rule(rule, 30.0) == -1.0
    assert evaluate_rule(rule, 50.0) == 0.0
    assert evaluate_rule(rule, None) is None
    lower = RegimeRule("valuation", "valuation", "pe_percentile_5y", 20.0, 80.0,
                       direction="lower_better")
    assert evaluate_rule(lower, 10.0) == 1.0
    assert evaluate_rule(lower, 90.0) == -1.0
    assert evaluate_rule(lower, 50.0) == 0.0


def test_pct_unit_rules_use_percentage_scale():
    """回归 N4：单位为 "%" 的字段，其阈值必须落在 0–100 口径。

    旧配置把 trend 写成 0.6/0.4、valuation 写成 0.2/0.8，而生产者
    `compute_vector` 输出的是百分数（`100.0 * above60 / total`），
    结果只要有 0.6% 的股票站上 MA60 就判看多，regime 长期 risk_on。
    本用例锁出厂阈值，并顺带验证 valuation 的实现（该字段
    implemented=False，生产者暂不产出，但阈值不能再按小数写）。
    """
    rules = {r.name: r for r in default_rules()}
    trend = rules["trend"]
    assert (trend.field, trend.risk_on_threshold, trend.risk_off_threshold) == \
        ("pct_above_ma60", 60.0, 40.0)
    assert evaluate_rule(trend, 20.0) == -1.0, "弱市 20% 必须看空，不得判看多"
    assert evaluate_rule(trend, 50.0) == 0.0
    assert evaluate_rule(trend, 80.0) == 1.0

    val = rules["valuation"]
    assert (val.field, val.risk_on_threshold, val.risk_off_threshold) == \
        ("pe_percentile_5y", 20.0, 80.0)

    # 声明为 "x"（小数）的字段仍按小数判，不能被这次改动一起放大 100 倍
    vol = rules["volatility"]
    assert (vol.risk_on_threshold, vol.risk_off_threshold) == (0.15, 0.30)

    # 出厂阈值与字段单位声明逐条对齐（漂移防线）
    from src.research.market_state import StateSchema
    schema = StateSchema()
    for r in rules.values():
        field = schema.get(r.field)
        if field is None:
            continue   # style 的 value_vs_growth 尚未注册（生产者无此字段）
        scale = 100.0 if field.unit == "%" else 1.0
        top = max(abs(r.risk_on_threshold), abs(r.risk_off_threshold))
        assert 0.0 < top <= scale, (
            f"{r.name} 阈值 {top} 与字段 {r.field} 单位 {field.unit} 不同量纲")


def test_classify_risk_on_when_all_agree():
    result = classify_regime(_vector({
        "pct_above_ma60": 80.0, "pe_percentile_5y": 10.0,
        "volatility_20d": 0.10, "value_vs_growth": 0.05}))
    assert result.regime == "risk_on"
    assert result.confidence == 1.0
    assert len(result.drivers) == 4
    assert result.missing_fields == []


def test_classify_risk_off_when_all_agree():
    result = classify_regime(_vector({
        "pct_above_ma60": 20.0, "pe_percentile_5y": 90.0,
        "volatility_20d": 0.40, "value_vs_growth": -0.05}))
    assert result.regime == "risk_off"
    assert result.confidence == 1.0


def test_classify_neutral_on_mixed_signals():
    result = classify_regime(_vector({
        "pct_above_ma60": 80.0, "pe_percentile_5y": 90.0,
        "volatility_20d": 0.10, "value_vs_growth": -0.05}))
    assert result.regime == "neutral"
    assert result.confidence == 0.0


def test_missing_fields_are_not_guessed():
    result = classify_regime(_vector({"pct_above_ma60": 80.0}))
    # 仅 1 条规则有数据 -> 证据不足，不给结论（不猜）
    assert result.regime == "unknown"
    assert result.confidence == 0.0
    assert set(result.missing_fields) == {
        "pe_percentile_5y", "volatility_20d", "value_vs_growth"}
    assert len(result.drivers) == 1


def test_two_rules_suffice():
    result = classify_regime(_vector({
        "pct_above_ma60": 80.0, "pe_percentile_5y": 10.0}))
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


# ── 端到端：生产者 → 消费者同一量纲（回归 N4）───────────────────────────
# 上面所有用例都是手喂 metrics 字典：旧代码阈值写小数、旧用例也喂小数，
# 两边"自洽地错"，谁都发现不了。只有把 compute_vector 接上才暴露。

@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "rg.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return db_mod


def _seed_market(db_mod, n_up: int, n_down: int) -> str:
    """铺 61 个连续交易日：上行票一路涨价（收盘必 > MA60），下行票反之。

    返回最后一日（作为 trade_date）。广度 = n_up / (n_up + n_down) × 100。
    """
    from datetime import date, timedelta
    days = [(date(2026, 8, 1) + timedelta(days=i)).isoformat()
            for i in range(61)]
    dao = db_mod.KlineDAO()
    for k in range(n_up + n_down):
        code = f"60{k:04d}"
        rising = k < n_up
        rows = []
        for i, d in enumerate(days):
            px = float(i + 1) if rising else float(61 - i)
            rows.append({"trade_date": d, "open": px, "close": px, "high": px,
                         "low": px, "volume": 1.0, "amount": 1e8,
                         "turnover": 0.1, "close_adj": px})
        dao.upsert_many(code, rows)
    return days[-1]


@pytest.mark.parametrize("n_up,n_down,expected_pct,expected_signal", [
    (1, 3, 25.0, -1.0),   # 弱市：bug 版 25.0 >= 0.6 会误判 +1（看多）
    (2, 2, 50.0, 0.0),    # 中性带：bug 版同样误判 +1
    (3, 1, 75.0, 1.0),    # 强市：正控（两种实现都给 +1）
])
def test_trend_rule_consumes_producer_percentage_scale(
        db, n_up, n_down, expected_pct, expected_signal):
    from src.research.market_state import compute_vector
    from src.research.regime import classify_regime
    trade_date = _seed_market(db, n_up, n_down)
    vector = compute_vector(trade_date)
    assert vector.metrics["pct_above_ma60"] == expected_pct, (
        "生产者输出的是百分数口径")
    result = classify_regime(vector)
    trend = next(d for d in result.drivers if d["rule"] == "trend")
    assert trend["value"] == expected_pct
    assert trend["signal"] == expected_signal
