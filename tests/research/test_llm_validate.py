"""LLM 输出校验回归测试（R0-11）。"""

import pytest

from src.research.llm.validate import (
    ValidationError, validate_output, validate_schema,
)


def _schema():
    return {
        "type": "object",
        "required": ["summary", "citations"],
        "properties": {
            "summary": {"type": "string"},
            "pe": {"type": "number"},
            "citations": {"type": "array", "items": {"type": "string"}},
            "verdict": {"type": "string",
                        "enum": ["supported", "rejected"]},
        },
    }


def test_valid_output_passes():
    out = {"summary": "PE 20 倍", "pe": 20.0, "citations": ["F-1"],
           "verdict": "supported"}
    result = validate_output(out, _schema(), {"F-1": {"pe": 20.0}})
    assert result.ok, result.errors
    assert result.citations == ["F-1"]


def test_schema_type_error_rejected():
    out = {"summary": "x", "pe": "not-a-number", "citations": ["F-1"]}
    result = validate_output(out, _schema(), {"F-1": {"pe": 20.0}})
    assert not result.ok
    assert any("number" in e for e in result.errors)


def test_enum_error_rejected():
    out = {"summary": "x", "citations": ["F-1"], "verdict": "maybe"}
    result = validate_output(out, _schema(), {"F-1": {"pe": 20.0}})
    assert not result.ok
    assert any("enum" in e for e in result.errors)


def test_missing_citations_rejected():
    result = validate_output({"summary": "x"}, _schema(), {"F-1": {"pe": 20.0}})
    assert not result.ok
    assert any("citations" in e for e in result.errors)


def test_unknown_citation_rejected():
    result = validate_output({"summary": "x", "citations": ["F-NOPE"]},
                             _schema(), {"F-1": {"pe": 20.0}})
    assert not result.ok
    assert any("F-NOPE" in e for e in result.errors)


def test_fabricated_number_rejected():
    result = validate_output({"summary": "x", "pe": 99.0, "citations": ["F-1"]},
                             _schema(), {"F-1": {"pe": 20.0}})
    assert not result.ok
    assert any("无证据来源" in e for e in result.errors)


def test_raise_if_invalid():
    with pytest.raises(ValidationError):
        validate_output({"summary": "x"}, _schema(), {}).raise_if_invalid()


def test_number_inside_text_rejected():
    """文本里夹带的数字也要溯源（编造数字拒绝）。"""
    result = validate_output(
        {"summary": "PE 99 倍", "citations": ["F-1"]},
        {"type": "object", "required": ["summary", "citations"],
         "properties": {"summary": {"type": "string"},
                        "citations": {"type": "array"}}},
        {"F-1": {"pe": 20.0}})
    assert not result.ok
    assert any("无证据来源" in e for e in result.errors)


def test_date_in_text_not_flagged():
    """日期片段不得被当成需要溯源的数字。"""
    result = validate_output(
        {"summary": "截至 2026-10-26，PE 20 倍", "citations": ["F-1"]},
        {"type": "object", "required": ["summary", "citations"],
         "properties": {"summary": {"type": "string"},
                        "citations": {"type": "array"}}},
        {"F-1": {"pe": 20.0}})
    assert result.ok, result.errors


def test_schema_validator_direct():
    assert validate_schema({"a": 1}, {"type": "object",
                                     "required": ["a"],
                                     "properties": {"a": {"type": "integer"}}}) == []
    assert validate_schema([], {"type": "array", "items": {"type": "string"}}) == []
    assert validate_schema("x", {"type": "array"}) != []
