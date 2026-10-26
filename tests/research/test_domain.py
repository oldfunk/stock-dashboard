"""Research OS 领域对象/状态机回归测试（R0-1）。"""

from datetime import date

import pytest

from src.research.domain import (
    Decision, Deployment, Experiment, ExperimentResult, Hypothesis,
    MarketState, StrategyVersion, Attribution,
    DomainError, ExperimentStatus, HypothesisStatus, format_id, next_id,
    parse_id, validate_transition,
)


def test_format_and_parse_id():
    value = format_id("H", date(2026, 10, 26), 7)
    assert value == "H-20261026-007"
    prefix, day, seq = parse_id(value)
    assert (prefix, day, seq) == ("H", "20261026", 7)


def test_parse_id_rejects_unknown_prefix_and_bad_format():
    with pytest.raises(DomainError):
        parse_id("X-20261026-001")
    with pytest.raises(DomainError):
        parse_id("H-2026-001")
    with pytest.raises(DomainError):
        format_id("Z", "20261026", 1)


def test_next_id_skips_existing():
    used = ["H-20261026-001", "H-20261026-002", "E-20261026-003"]
    assert next_id("H", "20261026", used) == "H-20261026-003"
    assert next_id("H", "20261027", used) == "H-20261027-001"


def test_next_id_accepts_iso_string_without_collision():
    """回归 D3：ISO 字符串日期必须与紧凑 ID 匹配，不得碰撞。"""
    used = ["DL-20261026-001"]
    assert next_id("DL", "2026-10-26", used) == "DL-20261026-002"
    assert next_id("DL", date(2026, 10, 26), used) == "DL-20261026-002"


def test_format_id_normalizes_iso_and_rejects_bad_date():
    assert format_id("H", "2026-10-26", 1) == "H-20261026-001"
    with pytest.raises(DomainError):
        format_id("H", "2026/10/26", 1)


def test_hypothesis_transition_valid_and_invalid():
    h = Hypothesis(
        id="H-20261026-001", version=1, prior="value",
        statement="x", universe="A股", signal={}, horizon_days=126,
        benchmark="sh000300", metric="excess", threshold=">0",
        regimes=["all"], costs={}, kill_criteria=["x"],
        status=HypothesisStatus.DRAFT.value,
    )
    validate_transition("hypothesis", h.status, HypothesisStatus.LOCKED.value)
    h.lock()
    assert h.status == HypothesisStatus.LOCKED.value
    with pytest.raises(DomainError):
        validate_transition("hypothesis", h.status,
                            HypothesisStatus.SUPPORTED.value)


def test_experiment_transition_rejects_jumping_to_done():
    e = Experiment(
        id="E-20261026-001", hypothesis_id="H-20261026-001", plan={},
        data_snapshot_id="DS-1", code_commit="abc", config_hash="h",
        random_seed=42, status=ExperimentStatus.PLANNED.value,
    )
    validate_transition("experiment", e.status, ExperimentStatus.RUNNING.value)
    with pytest.raises(DomainError):
        validate_transition("experiment", e.status, ExperimentStatus.DONE.value)


def test_dataclass_roundtrip():
    h = Hypothesis(
        id="H-20261026-002", version=2, prior="quality",
        statement="y", universe="非金融", signal={"a": 1},
        horizon_days=60, benchmark="sh000905", metric="sharpe",
        threshold=">0.5", regimes=["risk_on"], costs={"commission": 0.00025},
        kill_criteria=["k"], status=HypothesisStatus.LOCKED.value,
    )
    assert Hypothesis.from_dict(h.to_dict()) == h

    ms = MarketState(date="2026-10-26", metrics={"pe_pct": 40.0},
                      regime="range", drivers={"a": "b"}, quality={"ok": True})
    assert MarketState.from_dict(ms.to_dict()) == ms

    e = ExperimentResult(id="R-1", experiment_id="E-1", metric="excess",
                         value=0.02, ci_low=0.01, ci_high=0.03,
                         sample_size=100, verdict="supported")
    assert ExperimentResult.from_dict(e.to_dict()) == e


def test_other_entities_serialize():
    for obj in (
        Experiment(id="E-1", hypothesis_id="H-1", plan={}, data_snapshot_id="DS",
                   code_commit="c", config_hash="h", random_seed=1,
                   status=ExperimentStatus.PLANNED.value),
        StrategyVersion(id="S-VALUE-QUALITY-v1", version=1, parent_id="",
                        spec={}, status="draft"),
        Deployment(id="D-1", strategy_id="S-VALUE-QUALITY-v1", mode="paper",
                   start_date="2026-01-01", end_date="", capital=1_000_000,
                   broker="paper", status="running"),
        Attribution(id="A-1", deployment_id="D-1", period_start="2026-01-01",
                    period_end="2026-01-31", total_return=0.01,
                    excess_return=0.0, costs=0.0001),
        Decision(id="DL-1", decision_date="2026-10-26", type="promote",
                 target="S-VALUE-QUALITY-v1", change="x", rationale="y",
                 evidence={"E-1": "supported"}, expected="z",
                 review_date="2027-01-01", status="open"),
    ):
        data = obj.to_dict()
        cls = obj.__class__
        assert cls.from_dict(data) == obj
