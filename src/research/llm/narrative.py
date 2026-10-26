"""市场叙事任务（2026-10-27，R1-4）。

- ``market_summary``：只解释状态向量，**不引入新数字**。
- 事实集：``MS.<field>`` -> 值；叙事中的数字必须能溯源到这些事实（R0-11 校验）。
- **引用覆盖率**：golden case 要求 100%（向量中每个字段都被引用）。
- **无证据**：向量为空时输出"数据不足"，不生成叙事。

不调用真实 LLM（离线可跑）；LLM 接入在 R4。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.research.llm.validate import validate_output
from src.research.market_state import COVERAGE_MIN, MarketStateVector
from src.research.regime import RegimeResult

TASK_ID = "market_summary"
NO_EVIDENCE = "数据不足：状态向量无有效字段，不生成叙事。"

SCHEMA = {
    "type": "object",
    "required": ["summary", "citations"],
    "properties": {
        "summary": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
        "regime": {"type": "string",
                   "enum": ["risk_on", "risk_off", "neutral", "unknown"]},
    },
}


@dataclass
class NarrativeResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    coverage: float = 0.0
    facts: dict[str, Any] = field(default_factory=dict)
    output: dict | None = None


def should_generate_narrative(vector: MarketStateVector,
                              coverage_min: float = COVERAGE_MIN) -> bool:
    """覆盖率不足 / invalid / 无有效字段 -> 不生成叙事（R1-5 质量门接入）。"""
    return (bool(vector.metrics) and vector.coverage >= coverage_min
            and vector.status != "invalid")


def build_facts(vector: MarketStateVector,
                regime: RegimeResult) -> dict[str, Any]:
    """把状态向量 + regime 转成事实集（``MS.<field>``）。"""
    facts: dict[str, Any] = {f"MS.{k}": v for k, v in vector.metrics.items()}
    facts["MS.regime"] = regime.regime
    facts["MS.confidence"] = regime.confidence
    facts["MS.coverage"] = vector.coverage
    return facts


def required_citations(vector: MarketStateVector) -> list[str]:
    return [f"MS.{k}" for k in vector.metrics]


def citation_coverage(output: dict, vector: MarketStateVector) -> float:
    required = required_citations(vector)
    if not required:
        return 0.0
    cited = set(output.get("citations") or [])
    return round(len([c for c in required if c in cited]) / len(required), 4)


def validate_narrative(output: Any, vector: MarketStateVector,
                       regime: RegimeResult,
                       require_full_coverage: bool = True) -> NarrativeResult:
    """校验叙事：schema + 引用覆盖率 + 数字溯源（缺证据拒绝）。"""
    facts = build_facts(vector, regime)
    if not vector.metrics:
        return NarrativeResult(False, [NO_EVIDENCE], 0.0, facts)
    result = validate_output(output, SCHEMA, facts)
    coverage = citation_coverage(output, vector) if isinstance(output, dict) else 0.0
    errors = list(result.errors)
    if require_full_coverage and coverage < 1.0:
        errors.append(f"引用覆盖率 {coverage} < 1.0（未引用全部向量字段）")
    return NarrativeResult(ok=not errors, errors=errors, coverage=coverage,
                           facts=facts, output=output if isinstance(output, dict) else None)


def render_template_narrative(vector: MarketStateVector,
                              regime: RegimeResult) -> dict:
    """确定性模板叙事（只用向量值，不引入新数字）——离线/golden 用。"""
    if not vector.metrics:
        return {"summary": NO_EVIDENCE, "citations": [], "regime": regime.regime}
    lines = [f"市场状态（{vector.trade_date}）：regime={regime.regime}，"
             f"confidence={regime.confidence}。"]
    for name, value in vector.metrics.items():
        lines.append(f"- {name}={value}")
    if vector.missing:
        lines.append(f"缺失字段：{', '.join(vector.missing)}（显式 NULL，不补零）")
    return {
        "summary": "\n".join(lines),
        "citations": required_citations(vector) + [
            "MS.regime", "MS.confidence", "MS.coverage"],
        "regime": regime.regime,
    }
