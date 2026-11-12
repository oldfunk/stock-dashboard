"""参数敏感性（Q4 裁定，2026-11-12）：主判据符号翻转即不晋升。

邻域三样：tier ±10pp / horizon ±10 天 / 基准换 sh000922。
任邻域把主判据符号跑反 → 结果"不稳健"，不得晋升。
"""

from src.research.domain import ExperimentResult
from src.research.experiment import (
    ExperimentOutcome, ExperimentPlan, _apply_neighbor, run_sensitivity,
    sensitivity_neighbors,
)


def _plan(benchmark="sh000300"):
    return ExperimentPlan(
        experiment_id="E-T", hypothesis_id="H-T", data_snapshot_id="DS-T",
        dataset="analysis_outcome", universe="A", benchmark=benchmark,
        metric="net_60_>=40_vs_<=0_risk_off", threshold=">0", horizon_days=60,
        regimes=["all"], params={
            "tiers": [["<=0", None, 0.0], ["0~20", 0.0, 20.0],
                      ["20~40", 20.0, 40.0], [">=40", 40.0, None]],
            "horizons": [60], "net": [">=40", "<=0"], "net_regime": "risk_off"})


def _outcome(value, metric="net_60_>=40_vs_<=0_risk_off"):
    return ExperimentOutcome(
        experiment_id="E-T", status="done",
        results=[ExperimentResult(id="R1", experiment_id="E-T", metric=metric,
                                  value=value, sample_size=100)])


def test_neighbors_cover_three_kinds():
    labels = [lbl for lbl, _ in sensitivity_neighbors(_plan())]
    assert any("tier" in x for x in labels), labels
    assert any("horizon" in x for x in labels), labels
    assert any("benchmark_sh000922" in x for x in labels), labels


def test_tier_shift_moves_boundaries():
    _, ov = sensitivity_neighbors(_plan())[0]
    assert ov["tiers"][0][2] == -10.0, "tier-10pp 应把 <=0 档上界压到 -10"


def test_benchmark_swap_uses_reserved_key():
    _, ov = [x for x in sensitivity_neighbors(_plan())
             if "benchmark" in x[0]][0]
    assert ov["__benchmark__"] == "sh000922"


def test_apply_neighbor_does_not_mutate_original():
    p = _plan()
    before = [list(t) for t in p.params["tiers"]]
    q = _apply_neighbor(p, {"tiers": [["x", None, None]]})
    assert p.params["tiers"] == before, "原 plan 不得被邻域改到"
    assert q.params["tiers"] == [["x", None, None]]


def test_no_sign_flip_is_robust():
    res = run_sensitivity(_plan(), lambda p: _outcome(10.0), _outcome(10.0))
    assert res["sign_flipped"] is False
    assert res["robust"] is True


def test_sign_flip_is_not_robust():
    """tier-10pp 邻域把符号跑反 → 不晋升。"""

    def run_one(p):
        shifted = p.params["tiers"][0][2]
        return _outcome(-1.0 if shifted == -10.0 else 10.0)

    res = run_sensitivity(_plan(), run_one, _outcome(10.0))
    assert res["sign_flipped"] is True
    assert res["robust"] is False
    assert any(r["flipped"] for r in res["rows"])


def test_zero_value_neighbour_is_not_a_flip():
    """邻域值恰为 0（符号 0）不算翻转——它同样不支持原假设。"""
    res = run_sensitivity(_plan(), lambda p: _outcome(0.0), _outcome(10.0))
    assert res["sign_flipped"] is False
