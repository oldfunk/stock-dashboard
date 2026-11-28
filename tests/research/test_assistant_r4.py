"""R4-lite：实验复核事实、引用/建议校验、假模型调用。"""
from __future__ import annotations

from src.research import assistant
from src.research.dao import ResearchDAO


def _seed():
    dao = ResearchDAO()
    dao.insert_hypothesis({
        "id": "H-T1", "prior": "p", "statement": "高 MOS 超额更高",
        "universe": "A", "metric": "excess_60",
        "threshold": ">0", "status": "locked", "owner": "human",
        "created_at": "2026-11-01T00:00:00"})
    dao.insert_experiment({
        "id": "E-T1", "hypothesis_id": "H-T1", "status": "done",
        "verdict": "inconclusive", "data_snapshot_id": "DS-T1",
        "code_commit": "abc", "config_hash": "h",
        "plan": {"metric": "excess_60", "splits": ["regime"]},
        "notes": "样本 30", "created_at": "2026-11-01T00:00:00"})
    dao.insert_result({
        "id": "R-T1", "experiment_id": "E-T1", "metric": "excess_60",
        "value": 3.8, "ci_low": -3.1, "ci_high": 10.5,
        "sample_size": 30, "verdict": "inconclusive",
        "created_at": "2026-11-01T00:00:00"})
    return dao


def test_experiment_facts_and_prompt(minimal_db):
    _seed()
    facts = assistant.experiment_facts("E-T1")
    assert facts["hypothesis"]["id"] == "H-T1"
    assert facts["results"][0]["value"] == 3.8
    prompt = assistant.build_prompt(facts)
    assert "[H-002]" in prompt and "[E-H002b]" in prompt
    assert "严禁买入" in prompt


def test_validate_memo_flags_unknown_and_advice(minimal_db):
    _seed()
    facts = assistant.experiment_facts("E-T1")
    ok = assistant.validate_memo(
        "结论 [H-T1] [E-T1] [R-T1]，value 3.8", facts)
    assert ok["ok"] is True and ok["unknown_citations"] == []
    bad = assistant.validate_memo(
        "建议买入 [H-T1] [R-999] 99.9%", facts)
    assert "R-999" in bad["unknown_citations"]
    assert bad["advice_hits"]


class FakeAnalyzer:
    configured = True

    def ask_raw(self, prompt, system=None, timeout=None):
        return ("# 复核\n结论 [H-T1] [E-T1] [R-T1]，value 3.8。\n"
                "证据不足，不下结论。", "fake", {})


def test_review_experiment_returns_memo(minimal_db):
    _seed()
    res = assistant.review_experiment("E-T1", analyzer=FakeAnalyzer())
    assert res["ok"] is True
    assert "H-T1" in res["content_md"]
    assert res["checks"]["unknown_citations"] == []
