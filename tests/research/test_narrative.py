"""市场叙事任务回归测试（R1-4）。"""

import pytest

from src.research.llm.narrative import (
    NO_EVIDENCE, build_facts, citation_coverage, render_template_narrative,
    validate_narrative,
)
from src.research.market_state import MarketStateVector
from src.research.regime import RegimeResult


def _vector():
    return MarketStateVector(
        trade_date="2026-10-26",
        metrics={"pe_median": 15.0, "roe_median": 10.0},
        missing=["pb_median"], coverage=0.5, status="pass")


def _regime():
    return RegimeResult("2026-10-26", "risk_on", 1.0)


def test_build_facts_covers_vector():
    facts = build_facts(_vector(), _regime())
    assert facts["MS.pe_median"] == 15.0
    assert facts["MS.roe_median"] == 10.0
    assert facts["MS.regime"] == "risk_on"
    assert facts["MS.confidence"] == 1.0


def test_template_narrative_passes_validation():
    vector, regime = _vector(), _regime()
    output = render_template_narrative(vector, regime)
    result = validate_narrative(output, vector, regime)
    assert result.ok, result.errors
    assert result.coverage == 1.0


def test_no_evidence_when_vector_empty():
    vector = MarketStateVector(trade_date="2026-10-26")
    result = validate_narrative({"summary": "x", "citations": []},
                                vector, _regime())
    assert not result.ok
    assert NO_EVIDENCE in result.errors


def test_fabricated_number_rejected():
    vector, regime = _vector(), _regime()
    output = {"summary": "PE 99 倍",
              "citations": ["MS.pe_median", "MS.roe_median", "MS.regime"]}
    result = validate_narrative(output, vector, regime)
    assert not result.ok
    assert any("无证据来源" in e for e in result.errors)


def test_incomplete_coverage_rejected():
    vector, regime = _vector(), _regime()
    output = {"summary": "PE 15 倍", "citations": ["MS.pe_median"]}
    result = validate_narrative(output, vector, regime)
    assert not result.ok
    assert any("覆盖率" in e for e in result.errors)
    assert citation_coverage(output, vector) == 0.5


def test_template_no_evidence_message():
    vector = MarketStateVector(trade_date="2026-10-26")
    output = render_template_narrative(vector, _regime())
    assert output["summary"] == NO_EVIDENCE
    assert output["citations"] == []
