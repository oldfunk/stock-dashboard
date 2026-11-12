"""CI 判据（``ci_rule``，批18-b / Q1 裁定，2026-11-12）。

合成库实证（2026-11-12）：零关系库净增量 0.40pp、CI [-0.38, 1.16] **覆盖 0**，
旧逻辑仍判 ``supported``——"点估计过、CI 跨 0"正是 H-002 首跑暴露的缺陷。
本批把 CI 变成**预注册**字段：``exclude_zero`` 要求 CI 不覆盖 0 才 supported。
"""

from src.research.domain import CI_RULES, ExperimentResult
from src.research.experiment import (
    ExperimentOutcome, ExperimentPlan, derive_verdict, mark_result_verdicts,
)


def _plan(ci_rule="none", metric="net", threshold=">0"):
    return ExperimentPlan(
        experiment_id="E-T", hypothesis_id="H-T", data_snapshot_id="DS-T",
        dataset="analysis_outcome", universe="A", benchmark="sh000300",
        metric=metric, threshold=threshold, ci_rule=ci_rule,
        horizon_days=60, regimes=["all"], params={})


def _outcome(metric, value, ci_low, ci_high):
    return ExperimentOutcome(
        experiment_id="E-T", status="done",
        results=[ExperimentResult(id="R1", experiment_id="E-T", metric=metric,
                                  value=value, ci_low=ci_low, ci_high=ci_high,
                                  sample_size=100)])


def test_rules_are_exactly_two():
    assert CI_RULES == ("none", "exclude_zero")


def test_default_is_none():
    assert _plan().ci_rule == "none"


def test_none_keeps_point_estimate_semantics():
    """none（默认）：CI 跨 0 也判 supported（老口径，不让新老实验分叉）。"""
    assert derive_verdict(_plan("none"), _outcome("net", 0.4, -0.4, 1.2)) == "supported"


def test_exclude_zero_inconclusive_when_ci_covers_zero():
    """exclude_zero：CI 覆盖 0 → inconclusive（方向为正、不显著）。"""
    assert derive_verdict(
        _plan("exclude_zero"), _outcome("net", 0.4, -0.4, 1.2)) == "inconclusive"


def test_exclude_zero_supported_when_ci_clear_of_zero():
    assert derive_verdict(
        _plan("exclude_zero"), _outcome("net", 15.7, 12.3, 19.4)) == "supported"


def test_exclude_zero_inconclusive_when_ci_missing():
    """没 CI 就不能声称"不覆盖 0"——落 inconclusive，不静默放行。"""
    assert derive_verdict(
        _plan("exclude_zero"), _outcome("net", 1.0, None, None)) == "inconclusive"


def test_exclude_zero_still_rejects_wrong_sign():
    """点估计本身不满足阈值 → rejected（CI 规则不改变方向判定）。"""
    assert derive_verdict(
        _plan("exclude_zero"), _outcome("net", -5.0, -9.0, -1.0)) == "rejected"


def test_unknown_rule_falls_back_to_point_estimate():
    """未登记的名字按 none 处理（不静默放行、也不炸）。"""
    assert derive_verdict(_plan("weird"), _outcome("net", 0.4, -0.4, 1.2)) == "supported"


# ── Q3：result 级 verdict 只填预注册判据那一条 ──────────────

def test_only_primary_metric_gets_verdict():
    plan = _plan("exclude_zero")
    outcome = ExperimentOutcome(
        experiment_id="E-T", status="done",
        results=[
            ExperimentResult(id="R1", experiment_id="E-T", metric="net",
                             value=15.7, ci_low=12.3, ci_high=19.4, sample_size=100),
            ExperimentResult(id="R2", experiment_id="E-T", metric="excess_60_<=0",
                             value=1.0, ci_low=0.5, ci_high=1.5, sample_size=100),
        ])
    outcome.verdict = derive_verdict(plan, outcome)
    mark_result_verdicts(plan, outcome)
    assert outcome.results[0].verdict == "supported", "判据那条要填"
    assert outcome.results[1].verdict == "", "辅助指标不许填 verdict"
    assert "未预注册" in outcome.results[1].notes


def test_aux_metric_with_existing_notes_keeps_them():
    plan = _plan("none")
    outcome = ExperimentOutcome(
        experiment_id="E-T", status="done",
        results=[
            ExperimentResult(id="R1", experiment_id="E-T", metric="net",
                             value=1.0, sample_size=10),
            ExperimentResult(id="R2", experiment_id="E-T", metric="aux",
                             value=2.0, sample_size=10, notes="分域明细"),
        ])
    outcome.verdict = derive_verdict(plan, outcome)
    mark_result_verdicts(plan, outcome)
    assert outcome.results[1].notes == "分域明细", "已有 notes 不被覆盖"
