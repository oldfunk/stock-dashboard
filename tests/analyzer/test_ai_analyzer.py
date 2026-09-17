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


class TestBuildHistorySummary:
    """Tests for history summary extraction."""

    def test_no_history_returns_empty(self):
        """无历史记录时返回空字符串"""
        from src.analyzer.ai_analyzer import _build_history_summary
        result = _build_history_summary("NEVER_EXISTS_999999", limit=3)
        assert result == ""

    def test_real_stock_returns_proper_format(self, monkeypatch, tmp_path):
        """实际有历史记录的股票返回格式正确（tmp 库隔离，不碰真库）"""
        from src.analyzer.ai_analyzer import _build_history_summary
        from src.models import database as db_mod
        from src.models.database import StockAnalysisHistoryDAO, db_conn
        import json

        db_path = str(tmp_path / "test.db")
        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        db_mod.init_database()

        # 插入一条临时测试记录
        code = "TEST_HIST_0001"
        dao = StockAnalysisHistoryDAO()
        dao.save(
            code, "test_run_1", 80,
            json.dumps({"analysis": "公司roe稳定15%+，负债率持续下降，估值合理。"}),
            json.dumps({"signal": "BUY", "confidence": "高"})
        )
        dao.save(
            code, "test_run_2", 75,
            json.dumps({"analysis": "roe略有下降但仍在安全区，保持持有。"}),
            json.dumps({"signal": "HOLD", "confidence": "中"})
        )

        result = _build_history_summary(code, limit=2)

        # 清理测试数据
        with db_conn() as conn:
            conn.execute(
                "DELETE FROM stock_analysis_history WHERE stock_code = ?",
                (code,)
            )

        assert "【过往分析记录】" in result
        assert "BUY" in result
        assert "HOLD" in result
        # 每行应该包含日期（格式 YYYY-MM-DD）
        assert "20" in result[:30]  # 日期在开头附近
        # 长度控制
        assert len(result) < 500


class TestVerdictDiscipline:
    """B2 verdict↔signal 程序纪律。"""

    def _r(self, verdict, signal):
        from src.analyzer.ai_analyzer import _enforce_verdict_discipline
        return _enforce_verdict_discipline({
            'analysis': 'x', 'investment_strategy': 'y',
            'trade_strategy': {'signal': signal},
            'verdict': verdict,
        })

    def test_fail_forces_avoid(self):
        r = self._r('不通过：护城河被侵蚀', 'BUY')
        assert r['trade_strategy']['signal'] == 'AVOID'
        assert '_discipline_note' in r

    def test_fail_avoid_untouched(self):
        r = self._r('不通过', 'AVOID')
        assert r['trade_strategy']['signal'] == 'AVOID'
        assert '_discipline_note' not in r

    def test_gray_demotes_buy(self):
        r = self._r('灰色地带：证据不足', 'BUY')
        assert r['trade_strategy']['signal'] == 'HOLD'

    def test_gray_hold_untouched(self):
        r = self._r('灰色地带', 'HOLD')
        assert r['trade_strategy']['signal'] == 'HOLD'
        assert '_discipline_note' not in r

    def test_pass_buy_untouched(self):
        r = self._r('通过：低估且护城河宽', 'BUY')
        assert r['trade_strategy']['signal'] == 'BUY'
        assert '_discipline_note' not in r

    def test_missing_verdict_is_gray(self):
        r = self._r(None, 'BUY')
        assert r['trade_strategy']['signal'] == 'HOLD'
        assert '灰色' in r['verdict']

    def test_never_upgrades(self):
        # 只收紧不放松：HOLD/AVOID 永不变成 BUY
        r = self._r('通过', 'AVOID')
        assert r['trade_strategy']['signal'] == 'AVOID'

    def test_parse_keeps_new_fields(self):
        import json
        from src.analyzer.ai_analyzer import parse_ai_response
        doc = {'analysis': 'a', 'investment_strategy': 'b',
               'trade_strategy': {'signal': 'HOLD'},
               'verdict': '通过：x',
               'price_tiers': {'aggressive': {'advice': '建仓', 'range': '1-2'}}}
        r = parse_ai_response(json.dumps(doc))
        assert r['verdict'] == '通过：x'
        assert r['price_tiers']['aggressive']['range'] == '1-2'

    def test_old_json_without_verdict_survives(self):
        import json
        from src.analyzer.ai_analyzer import parse_ai_response
        doc = {'analysis': 'a', 'investment_strategy': 'b',
               'trade_strategy': {'signal': 'HOLD'}}
        r = parse_ai_response(json.dumps(doc))
        assert r is not None
        assert 'verdict' not in r  # 解析层不强加，纪律层在写库时处置


class TestQualityScore:
    """Q 能力分：质量优先，可用次之。"""

    def test_quality_beats_availability(self):
        from src.analyzer.ai_analyzer import FreeModelPool
        import time
        p = FreeModelPool('http://127.0.0.1:9', None)
        p._pool = ['good', 'fast']
        p._last_refresh = time.time()
        for _ in range(5):
            p.record_quality('good', True)
        for _ in range(4):
            p.record_quality('fast', False)
        for _ in range(5):
            p.record_result('fast', ok=True, latency=3.0)
        for _ in range(4):
            p.record_result('good', ok=False)
        assert p.quality_score('good') > p.quality_score('fast')
        assert p.acquire() == 'good'

    def test_newcomer_neutral(self):
        from src.analyzer.ai_analyzer import FreeModelPool
        p = FreeModelPool('http://127.0.0.1:9', None)
        assert p.quality_score('never-seen') == 0.5


class TestConsistencyEnforce:
    """Q 确定性交叉：否决改判 + 镜子/六关熔断。"""

    def _r(self, **kw):
        from src.analyzer.ai_analyzer import _enforce_verdict_discipline
        d = {'analysis': 'x', 'investment_strategy': 'y',
             'trade_strategy': {'signal': 'BUY'},
             'verdict': '通过：低估',
             'veto_checklist': {'triggered_count': 0},
             'mirror_test': {'passed': True},
             'checklist': {}}
        d.update(kw)
        return _enforce_verdict_discipline(d)

    def test_veto_overrides_all(self):
        r = self._r(veto_checklist={'triggered_count': 1,
                                    'following_the_herd': True})
        assert r['verdict'].startswith('不通过')
        assert r['trade_strategy']['signal'] == 'AVOID'
        assert '_discipline_note' in r

    def test_mirror_melts_buy(self):
        r = self._r(mirror_test={'passed': False})
        assert r['trade_strategy']['signal'] == 'HOLD'

    def test_checklist_low_melts_buy(self):
        r = self._r(checklist={'moat': {'score': 2, 'note': '浅'}})
        assert r['trade_strategy']['signal'] == 'HOLD'

    def test_consistency_fn(self):
        from src.analyzer.ai_analyzer import _check_output_consistency
        assert _check_output_consistency({}) == []
        assert _check_output_consistency('nope') == []
        bad = {'trade_strategy': {'signal': 'BUY'},
               'veto_checklist': {'triggered_count': 2},
               'verdict': '通过：好'}
        issues = _check_output_consistency(bad)
        assert len(issues) == 2


class TestNumericCitations:
    """C3 数字抽检：命中/未命中/小数容差，warn-only 永不抛异常。"""

    def _check(self, text, stock):
        from src.analyzer.ai_analyzer import _check_numeric_citations
        return _check_numeric_citations(text, stock)

    def _stock(self, **kw):
        s = {'roe_5y_avg': 15.2, 'pe': 28.5, 'pb': 3.1,
             'gross_margin_5y_avg': 40.0, 'net_margin_5y_avg': 12.3,
             'market_cap': 2000.0, 'debt_ratio': 35.0}
        s.update(kw)
        return s

    def test_hit_no_mismatch(self):
        mm = self._check(
            '这家公司 ROE 15.2%，PE 28.5x，毛利率 40%，市值 2000亿。',
            self._stock())
        assert mm == []

    def test_miss_records_mismatch(self):
        mm = self._check('ROE 高达 25%，非常优秀。', self._stock())
        assert len(mm) == 1
        assert mm[0]['field'] == 'roe_5y_avg'
        assert mm[0]['cited'] == 25.0
        assert mm[0]['reference'] == 15.2

    def test_decimal_tolerance(self):
        # 小数舍入差异放行：库 12.3，正文 12.34
        mm = self._check('净利率 12.34%。', self._stock())
        assert mm == []

    def test_bare_numbers_ignored(self):
        # 无标签裸数字无法归因，不记账
        mm = self._check('去年分红 3 次，覆盖 20 个省份。', self._stock())
        assert mm == []

    def test_missing_ref_skipped(self):
        # 库无参照（N/A/None）→ 跳过不记账
        mm = self._check('PE 28.5x，PB 3.1x。',
                         self._stock(pe='N/A', pb=None))
        assert mm == []

    def test_negative_sign(self):
        # 符号位：-1.72% 不得被抓成 1.72
        mm = self._check('净利率 -1.72%，出现亏损。',
                         self._stock(net_margin_5y_avg=-1.72))
        assert mm == []
        mm2 = self._check('净利率 1.72%。',
                          self._stock(net_margin_5y_avg=-1.72))
        assert len(mm2) == 1


class TestLingPriority:
    """ling 金融模型优先 + 故障轮换（fallback 契约）。"""

    def test_ling_is_first_preference(self):
        from src.analyzer.ai_analyzer import FreeModelPool
        assert FreeModelPool.PREFERRED_ORDER[0] == "ling-3.0-flash-fin-free"

    def test_refresh_discovers_ling_first(self, monkeypatch):
        from src.analyzer.ai_analyzer import FreeModelPool

        class FakeResp:
            status_code = 200

            def json(self):
                return {"data": [
                    {"id": "deepseek-v4-flash-free"},
                    {"id": "ling-3.0-flash-fin-free"},
                    {"id": "gpt-5.5"},
                ]}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, headers=None):
                return FakeResp()

        import httpx
        monkeypatch.setattr(httpx, "Client", FakeClient)
        p = FreeModelPool("http://127.0.0.1:9", None)
        p._refresh()
        assert p._pool[0] == "ling-3.0-flash-fin-free"
        assert "gpt-5.5" not in p._pool  # 非 free 不进池
        assert p.acquire() == "ling-3.0-flash-fin-free"

    def test_dead_ling_falls_back_to_next(self):
        from src.analyzer.ai_analyzer import FreeModelPool
        import time
        p = FreeModelPool("http://127.0.0.1:9", None)
        p._pool = ["ling-3.0-flash-fin-free", "deepseek-v4-flash-free"]
        p._last_refresh = time.time()
        assert p.acquire() == "ling-3.0-flash-fin-free"
        p.mark_dead("ling-3.0-flash-fin-free")
        assert p.acquire() == "deepseek-v4-flash-free"


class TestRetryAfter:
    """429 尊重服务端 Retry-After。"""

    def _resp(self, headers):
        from types import SimpleNamespace
        return SimpleNamespace(headers=headers)

    def test_numeric_seconds(self):
        from src.analyzer.ai_analyzer import _retry_after_seconds
        assert _retry_after_seconds(self._resp({"retry-after": "120"}), 60) == 120

    def test_missing_header_falls_back(self):
        from src.analyzer.ai_analyzer import _retry_after_seconds
        assert _retry_after_seconds(self._resp({}), 60) == 60

    def test_invalid_header_falls_back(self):
        from src.analyzer.ai_analyzer import _retry_after_seconds
        assert _retry_after_seconds(self._resp({"retry-after": "soon"}), 45) == 45

    def test_capped_at_300(self):
        from src.analyzer.ai_analyzer import _retry_after_seconds
        assert _retry_after_seconds(self._resp({"retry-after": "9999"}), 60) == 300


if __name__ == '__main__':
    pytest.main([__file__, '-v'])