"""测试夹具回归测试（R0-12）。"""

from datetime import date

import pytest


def test_experiment_fixture_runs_offline(experiment_fixture):
    from src.research.experiment import TopKBacktestRunner, run_experiment
    outcome = run_experiment(TopKBacktestRunner(), experiment_fixture["plan"])
    assert outcome.status == "done"
    assert outcome.verdict == "supported"


def test_fake_llm_records_calls_without_network(fake_llm):
    fake_llm.complete("market_summary", "prompt", tools=["get_market_state"])
    assert fake_llm.call_count == 1
    assert fake_llm.calls[0]["task"] == "market_summary"


def test_fake_llm_raises_when_exhausted(fake_llm):
    with pytest.raises(RuntimeError):
        fake_llm.complete("t", "p")


def test_fixed_clock_is_deterministic(fixed_clock):
    assert fixed_clock.now_cn().isoformat() == "2026-10-26T12:00:00"
    assert fixed_clock.today() == date(2026, 10, 26)


def test_fake_tool_gateway():
    from src.research.llm.fake import FakeToolGateway
    gateway = FakeToolGateway({"hypothesis": [{"id": "H-1"}]})
    assert gateway.call("t", "hypothesis") == [{"id": "H-1"}]
    assert gateway.tools_called == ["hypothesis"]


def test_validation_pipeline_offline(fake_llm):
    """fake LLM 输出 -> 校验，全离线。"""
    from src.research.llm.validate import validate_output
    fake_llm.responses = [{"summary": "x", "pe": 20.0, "citations": ["F-1"]}]
    output = fake_llm.complete("t", "p")
    result = validate_output(
        output,
        {"type": "object", "required": ["summary", "citations"],
         "properties": {"summary": {"type": "string"},
                        "pe": {"type": "number"},
                        "citations": {"type": "array"}}},
        {"F-1": {"pe": 20.0}})
    assert result.ok
