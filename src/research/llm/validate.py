"""LLM 输出校验（2026-10-26，R0-11）。

校验四道：
1. **JSON schema**（type/required/properties/items/enum）。
2. **fact ID 引用**：``citations`` 必须非空，且每个 ID 存在于证据集。
3. **数字一致性**：输出中出现的每个数字必须能溯源到被引用证据的数值（容差内）。
4. **缺证据拒绝**（fail-closed）：无引用/引用不存在/数字无来源 → 拒绝。

不依赖 ``jsonschema``（避免新增依赖）；schema 子集足够研究助手使用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TOLERANCE = 1e-6


class ValidationError(ValueError):
    """输出校验失败（聚合错误）。"""


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    numbers_checked: int = 0

    def raise_if_invalid(self) -> None:
        if not self.ok:
            raise ValidationError("; ".join(self.errors))


def validate_schema(instance: Any, schema: dict, path: str = "$") -> list[str]:
    """最小 JSON schema 校验（type/required/properties/items/enum）。"""
    if not schema:
        return []
    errors: list[str] = []
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            return [f"{path}: 期望 object，实际 {type(instance).__name__}"]
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: 缺少 required 字段 {key}")
        for key, sub in schema.get("properties", {}).items():
            if key in instance:
                errors += validate_schema(instance[key], sub, f"{path}.{key}")
    elif expected == "array":
        if not isinstance(instance, list):
            return [f"{path}: 期望 array，实际 {type(instance).__name__}"]
        for i, item in enumerate(instance):
            errors += validate_schema(item, schema.get("items", {}), f"{path}[{i}]")
    elif expected == "string":
        if not isinstance(instance, str):
            errors.append(f"{path}: 期望 string，实际 {type(instance).__name__}")
    elif expected == "number":
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            errors.append(f"{path}: 期望 number，实际 {type(instance).__name__}")
    elif expected == "integer":
        if isinstance(instance, bool) or not isinstance(instance, int):
            errors.append(f"{path}: 期望 integer，实际 {type(instance).__name__}")
    elif expected == "boolean":
        if not isinstance(instance, bool):
            errors.append(f"{path}: 期望 boolean，实际 {type(instance).__name__}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: 值 {instance!r} 不在 enum {schema['enum']}")
    return errors


def _collect_numbers(value: Any) -> list[float]:
    out: list[float] = []
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            out += _collect_numbers(v)
    elif isinstance(value, list):
        for v in value:
            out += _collect_numbers(v)
    return out


def _fact_numbers(facts: dict[str, Any], citations: list[str]) -> list[float]:
    out: list[float] = []
    for cid in citations:
        out += _collect_numbers(facts.get(cid))
    return out


def _matches(value: float, pool: list[float], tolerance: float) -> bool:
    return any(math.isclose(value, p, rel_tol=tolerance, abs_tol=tolerance)
               for p in pool)


def validate_output(output: Any, schema: dict, facts: dict[str, Any],
                    tolerance: float = DEFAULT_TOLERANCE) -> ValidationResult:
    """校验 LLM 输出；返回结果（不抛异常，由调用方决定）。"""
    errors: list[str] = []
    errors += validate_schema(output, schema)

    if not isinstance(output, dict):
        return ValidationResult(False, errors + ["输出不是 JSON 对象"], [], 0)

    citations = output.get("citations")
    if not isinstance(citations, list) or not citations:
        errors.append("缺少证据引用（citations 非空）")
        citations = []
    for cid in citations:
        if cid not in facts:
            errors.append(f"引用不存在的证据 ID: {cid}")

    numbers = _collect_numbers({k: v for k, v in output.items()
                                if k != "citations"})
    pool = _fact_numbers(facts, citations)
    untraceable = [n for n in numbers if not _matches(n, pool, tolerance)]
    if untraceable:
        errors.append(f"数字无证据来源: {untraceable[:5]}")

    return ValidationResult(ok=not errors, errors=errors,
                            citations=citations, numbers_checked=len(numbers))
