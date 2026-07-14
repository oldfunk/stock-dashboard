"""
Core unit tests for AI Analyzer - focusing on JSON parsing edge cases.

Tests parse_ai_response with various malformed inputs.
"""

import pytest
from src.analyzer.ai_analyzer import parse_ai_response


class TestParseAIResponse:
    """Tests for robust JSON parsing from LLM output."""

    def _base_valid_json(self):
        return {
            "analysis": "测试分析",
            "moat_evaluation": [
                {"type": "转换成本", "score": 3, "trend": "稳定", "evidence": "..."},
                {"type": "网络效应", "score": 2, "trend": "稳定", "evidence": "..."},
                {"type": "无形资产（品牌/专利/许可）", "score": 3, "trend": "稳定", "evidence": "..."},
                {"type": "成本优势", "score": 4, "trend": "稳定", "evidence": "..."},
                {"type": "有效规模（自然寡头）", "score": 2, "trend": "稳定", "evidence": "..."},
            ],
            "management_score": {
                "capital_allocation": "7",
                "shareholder_friendliness": "6",
                "summary": "管理层表现尚可"
            },
            "intrinsic_value": {
                "conservative": "1000亿",
                "base_case": "1500亿",
                "optimistic": "2000亿",
                "margin_of_safety": "20%",
                "method": "Owner Earnings × 10倍"
            },
            "reverse_thinking": "政策风险、原材料涨价、竞品冲击",
            "investment_strategy": "中等仓位，长期持有",
            "trade_strategy": {
                "signal": "BUY",
                "confidence": "中",
                "buy_zone": "1500-1600",
                "target_price": "2000",
                "stop_loss": "跌破1300止损",
                "take_profit": "涨至2000分批止盈"
            },
            "mirror_counts": "0,1,0,0,1"
        }

    def _minimal_valid_json(self):
        """Minimal JSON with only required fields."""
        return {
            "analysis": "测试分析",
            "investment_strategy": "持有",
            "trade_strategy": {
                "signal": "BUY",
                "confidence": "中",
                "buy_zone": "100",
                "target_price": "200",
                "stop_loss": "90",
                "take_profit": "210"
            },
            "moat_evaluation": [],
            "management_score": {},
            "intrinsic_value": {},
            "reverse_thinking": "风险",
            "mirror_counts": "0,0,0,0,0"
        }

    def test_valid_json_passes(self):
        """Valid JSON should parse correctly."""
        result = parse_ai_response(str(self._base_valid_json()).replace("'", '"'))
        assert result is not None
        assert result['analysis'] == "测试分析"
        assert result['trade_strategy']['signal'] == "BUY"

    def test_markdown_code_block_json(self):
        """JSON wrapped in ```json``` code block."""
        json_str = '```json\n' + str(self._base_valid_json()).replace("'", '"') + '\n```'
        result = parse_ai_response(json_str)
        assert result is not None
        assert result['analysis'] == "测试分析"

    def test_markdown_generic_code_block(self):
        """JSON wrapped in generic ``` code block."""
        json_str = '```\n' + str(self._base_valid_json()).replace("'", '"') + '\n```'
        result = parse_ai_response(json_str)
        assert result is not None

    def test_preamble_and_postamble_text(self):
        """JSON with explanatory text before and after."""
        text = "Here is the analysis:\n" + str(self._base_valid_json()).replace("'", '"') + "\nEnd of response."
        result = parse_ai_response(text)
        assert result is not None

    def test_single_quotes_instead_of_double(self):
        """Python-style single quotes should be converted."""
        result = parse_ai_response(str(self._base_valid_json()))
        assert result is not None

    def test_trailing_commas_removed(self):
        """Trailing commas in objects/arrays should be handled."""
        # Include all required fields
        json_with_trailing = '{"analysis": "test", "investment_strategy": "hold", "trade_strategy": {"signal": "BUY", "confidence": "中", "buy_zone": "100", "target_price": "200", "stop_loss": "90", "take_profit": "210",}, "moat_evaluation": [], "management_score": {}, "intrinsic_value": {}, "reverse_thinking": "risk", "mirror_counts": "0,0,0,0,0"}'
        result = parse_ai_response(json_with_trailing)
        assert result is not None
        assert result['analysis'] == "test"

    def test_truncated_json_completed(self):
        """Truncated JSON should be completed with closing braces."""
        # Include all required fields in truncated form
        truncated = '{"analysis": "test", "investment_strategy": "hold", "trade_strategy": {"signal": "BUY", "confidence": "中", "buy_zone": "100", "target_price": "200", "stop_loss": "90", "take_profit": "210", "moat_evaluation": [], "management_score": {}, "intrinsic_value": {}, "reverse_thinking": "risk", "mirror_counts": "0,0,0,0,0"}'
        result = parse_ai_response(truncated)
        assert result is not None
        assert result['analysis'] == "test"
        assert result['trade_strategy']['signal'] == "BUY"

    def test_newlines_in_string_values_escaped(self):
        """Actual newlines inside JSON string values should be escaped."""
        content = '{"analysis": "line1\\nline2\\nline3", "investment_strategy": "hold", "trade_strategy": {"signal": "BUY", "confidence": "中", "buy_zone": "100", "target_price": "200", "stop_loss": "90", "take_profit": "210"}, "moat_evaluation": [], "management_score": {}, "intrinsic_value": {}, "reverse_thinking": "risk", "mirror_counts": "0,0,0,0,0"}'
        result = parse_ai_response(content)
        assert result is not None
        assert "line1\nline2\nline3" in result['analysis'] or "line1\\nline2\\nline3" in result['analysis']

    def test_missing_required_fields_returns_none(self):
        """Missing analysis/investment_strategy/trade_strategy -> None."""
        incomplete = '{"analysis": "only analysis"}'
        result = parse_ai_response(incomplete)
        assert result is None

    def test_empty_string_returns_none(self):
        assert parse_ai_response("") is None
        assert parse_ai_response("   ") is None
        assert parse_ai_response(None) is None

    def test_non_json_text_returns_none(self):
        assert parse_ai_response("This is not JSON at all") is None

    def test_deeply_nested_truncation(self):
        """Test truncation with nested objects/arrays."""
        truncated = '{"analysis": "test", "investment_strategy": "hold", "trade_strategy": {"signal": "BUY", "confidence": "中", "buy_zone": "100", "target_price": "200", "stop_loss": "90", "take_profit": "210"}, "moat_evaluation": [{"type": "test", "score": 1, "trend": "稳定", "evidence": "..."}], "management_score": {}, "intrinsic_value": {}, "reverse_thinking": "risk", "mirror_counts": "0,0,0,0,0"}'
        result = parse_ai_response(truncated)
        assert result is not None
        assert result['analysis'] == "test"

    def test_mixed_single_double_quotes(self):
        """Mixed quote styles."""
        mixed = '{"analysis": "test", "investment_strategy": "hold", "trade_strategy": {"signal": "BUY", "confidence": "中", "buy_zone": "100", "target_price": "200", "stop_loss": "90", "take_profit": "210"}, "moat_evaluation": [], "management_score": {}, "intrinsic_value": {}, "reverse_thinking": "risk", "mirror_counts": "0,0,0,0,0"}'
        result = parse_ai_response(mixed)
        assert result is not None
        assert result['analysis'] == "test"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])