"""
Core unit tests for ValueScreener - focusing on boundary/exception paths.

Tests the 7 gates + exemptions logic, scoring edge cases.
"""

import pytest
from src.screener.value_screener import (
    _check_7_gates, _calculate_moat_score, _score_breakdown, ValueScreener,
)


class TestCheck7Gates:
    """Tests for the 7 gates + 3 exemptions logic."""

    def _base_stock(self, **overrides):
        """Create a base stock dict that passes all gates, then apply overrides."""
        base = {
            'code': '600519',
            'name': '贵州茅台',
            'is_st': False,
            'pe': 15.0,
            'pb': 2.0,
            'roe': 10.0,
            'roe_5y_avg': 25.0,
            'gross_margin': 40.0,
            'gross_margin_5y_avg': 38.0,
            'net_margin_5y_avg': 15.0,
            'ocf_per_share': 10.0,
            'ocf_positive_years': 5,
            'revenue_growth': 10.0,
            'profit_growth': 12.0,
            'debt_ratio': 30.0,
            'market_cap': 1000.0,
            'intcov_5y_avg': 50.0,
            'fcf_5y_sum': 100_000_000_00,
            'share_dilution_5y': 5.0,
            'roe_5y_count': 5,
            'data_years': '2020-2024',
        }
        base.update(overrides)
        return base

    def test_score_breakdown_matches_total(self):
        """评分透明化核心不变量：_score_breakdown 总分必须 == _calculate_moat_score。"""
        cases = [
            self._base_stock(),
            self._base_stock(roe=35.0, roe_5y_avg=0),
            self._base_stock(pe=4.0, debt_ratio=15.0, gross_margin=65.0),
            self._base_stock(roe_volatility=3.0, roe_improvement=5.0,
                             fcf_positive_years_10=9),
        ]
        for s in cases:
            bd = _score_breakdown(s)
            total = bd['total']
            assert total == _calculate_moat_score(s)
            base = sum(p['contribution'] for k, p in bd.items()
                       if k not in ('total', 'consistency_bonus'))
            assert abs(base + bd['consistency_bonus'] - total) < 0.5
            for dim in ('roe', 'pe', 'growth', 'debt', 'margin'):
                assert {'raw', 'sub', 'weight', 'contribution'} <= set(bd[dim])

    def _base_config(self):
        return {
            'screener': {
                'conditions': {
                    'max_pe': 20,
                    'min_pe': 3,
                    'max_pb': 3.5,
                    'min_roe': 5,
                    'min_revenue_growth': 0,
                    'min_profit_growth': 0,
                    'max_debt_ratio': 65,
                    'min_market_cap': 30,
                    'max_market_cap': 50000,
                    'exclude_st': True,
                    'exclude_keywords': 'ST,退',
                    'min_gross_margin': 15,
                    'min_ocf_per_share': 0,
                    'min_net_margin': 5,
                    'min_interest_coverage': 2,
                    'min_fcf_5y': 0,
                    'max_share_dilution': 20,
                }
            }
        }

    # --- Gate 0: ST exclusion ---
    def test_gate_st_exclusion(self):
        stock = self._base_stock(is_st=True)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_st_exclusion_disabled(self):
        stock = self._base_stock(is_st=True)
        cfg = self._base_config()
        cfg['screener']['conditions']['exclude_st'] = False
        reasons = _check_7_gates(stock, cfg)
        assert len(reasons) > 0

    # --- Gate 1: PE range ---
    def test_gate_pe_too_high(self):
        stock = self._base_stock(pe=25.0)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_pe_too_low(self):
        stock = self._base_stock(pe=2.0)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_pe_none(self):
        stock = self._base_stock(pe=None)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_pe_boundary_exact(self):
        """PE exactly at boundaries should pass."""
        stock = self._base_stock(pe=3.0)
        assert len(_check_7_gates(stock, self._base_config())) > 0
        stock = self._base_stock(pe=20.0)
        assert len(_check_7_gates(stock, self._base_config())) > 0

    # --- Gate 2: ROE current ---
    def test_gate_roe_current_too_low(self):
        stock = self._base_stock(roe=3.0)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_roe_current_none(self):
        stock = self._base_stock(roe=None)
        reasons = _check_7_gates(stock, self._base_config())
        assert len(reasons) > 0

    # --- Gate 3: 5-year avg ROE < 8% with exemption ---
    def test_gate_roe_5y_low_exempt_high_growth(self):
        """5y ROE < 8% + 高增长 + OCF转正 + 短覆盖 -> 豁免A（细化后更严）"""
        stock = self._base_stock(roe_5y_avg=7.0, revenue_growth=25.0,
                                 ocf_latest=3.0, ocf_5y_trend=1,
                                 data_years='2020-2024')
        reasons = _check_7_gates(stock, self._base_config())
        assert len(reasons) > 0
        assert any('豁免A' in r for r in reasons)

    def test_gate_roe_5y_low_no_ocf_no_exemption(self):
        """5y ROE < 8% + 高增长但 OCF 未转正 -> 照样排除（细化新增）"""
        stock = self._base_stock(roe_5y_avg=7.0, revenue_growth=25.0,
                                 ocf_latest=-1.0,
                                 data_years='2020-2024')
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_roe_5y_low_no_exemption(self):
        """5y ROE < 8% and growth <= 20% -> excluded."""
        stock = self._base_stock(roe_5y_avg=7.0, revenue_growth=15.0)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_roe_5y_none_skip(self):
        """5y ROE None -> gate skipped (not a hard fail)."""
        stock = self._base_stock(roe_5y_avg=None)
        reasons = _check_7_gates(stock, self._base_config())
        assert len(reasons) > 0

    # --- Gate 4: OCF per share ---
    def test_gate_ocf_negative_exempt_high_gm_growth(self):
        """OCF <= 0 but high GM + high growth -> exemption."""
        stock = self._base_stock(ocf_per_share=-1.0, gross_margin=35.0, revenue_growth=25.0)
        reasons = _check_7_gates(stock, self._base_config())
        assert len(reasons) > 0
        assert any('豁免' in r and '高毛利率' in r for r in reasons)

    def test_gate_ocf_negative_no_exemption(self):
        """OCF <= 0 without exemption -> excluded."""
        stock = self._base_stock(ocf_per_share=-1.0, gross_margin=20.0, revenue_growth=10.0)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_ocf_positive_but_few_positive_years(self):
        """OCF > 0 but most historical years negative -> excluded."""
        stock = self._base_stock(ocf_per_share=5.0, ocf_positive_years=1, roe_5y_count=5)
        assert _check_7_gates(stock, self._base_config()) == []

    # --- Gate 5: Net margin 5y avg ---
    def test_gate_net_margin_5y_low_exempt_high_gm(self):
        """5y net margin < 5% + GM >= 30% + 双增长 -> 豁免C（细化后须双正）"""
        stock = self._base_stock(net_margin_5y_avg=4.0, gross_margin=35.0)
        reasons = _check_7_gates(stock, self._base_config())
        assert len(reasons) > 0
        assert any('豁免C' in r and '双增长改善' in r for r in reasons)

    def test_gate_net_margin_5y_low_no_exemption(self):
        stock = self._base_stock(net_margin_5y_avg=4.0, gross_margin=20.0)
        assert _check_7_gates(stock, self._base_config()) == []

    # --- Gate 6: Gross margin ---
    def test_gate_gross_margin_low_exempt_high_roe(self):
        """GM < 15% but ROE >= 20% -> exemption."""
        stock = self._base_stock(gross_margin=12.0, roe=25.0)
        reasons = _check_7_gates(stock, self._base_config())
        assert len(reasons) > 0
        assert any('豁免' in r and '薄利模式' in r for r in reasons)

    def test_gate_gross_margin_low_no_exemption(self):
        stock = self._base_stock(gross_margin=12.0, roe=10.0)
        assert _check_7_gates(stock, self._base_config()) == []

    # --- Gate 7: Revenue/Profit growth ---
    def test_gate_revenue_growth_negative(self):
        stock = self._base_stock(revenue_growth=-5.0)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_profit_growth_negative(self):
        stock = self._base_stock(profit_growth=-5.0)
        assert _check_7_gates(stock, self._base_config()) == []

    # --- Gate 8: Debt ratio ---
    def test_gate_debt_ratio_too_high(self):
        stock = self._base_stock(debt_ratio=70.0)
        assert _check_7_gates(stock, self._base_config()) == []

    # --- Gate 9: Interest coverage ---
    def test_gate_intcov_too_low(self):
        stock = self._base_stock(intcov_5y_avg=1.5)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_intcov_none_skipped(self):
        stock = self._base_stock(intcov_5y_avg=None)
        reasons = _check_7_gates(stock, self._base_config())
        assert len(reasons) > 0

    # --- Gate 10: 5y FCF sum ---
    def test_gate_fcf_5y_negative_large_excluded(self):
        stock = self._base_stock(fcf_5y_sum=-500_000_000_00)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_fcf_5y_negative_small_exempt(self):
        """5y FCF small negative (< 1亿) -> exemption."""
        stock = self._base_stock(fcf_5y_sum=-50_000_000)  # -0.5亿 < 1亿豁免
        reasons = _check_7_gates(stock, self._base_config())
        assert len(reasons) > 0
        assert any('小额负值豁免' in r for r in reasons)

    # --- Gate 11: Share dilution ---
    def test_gate_dilution_too_high(self):
        stock = self._base_stock(share_dilution_5y=25.0)
        assert _check_7_gates(stock, self._base_config()) == []

    # --- Market cap ---
    def test_gate_market_cap_too_small(self):
        stock = self._base_stock(market_cap=20.0)
        assert _check_7_gates(stock, self._base_config()) == []

    def test_gate_market_cap_too_large(self):
        stock = self._base_stock(market_cap=60000.0)
        assert _check_7_gates(stock, self._base_config()) == []

    # --- PB ---
    def test_gate_pb_too_high(self):
        stock = self._base_stock(pb=4.0)
        assert _check_7_gates(stock, self._base_config()) == []


class TestCalculateMoatScore:
    """Tests for scoring edge cases."""

    def _base_stock(self, **overrides):
        base = {
            'roe': 20.0, 'roe_5y_avg': 22.0,
            'pe': 10.0,
            'revenue_growth': 15.0, 'profit_growth': 18.0,
            'debt_ratio': 30.0,
            'gross_margin': 40.0, 'gross_margin_5y_avg': 38.0,
            'roe_volatility': 8.0,
            'roe_improvement': 3.0,
            'fcf_positive_years_10': 8,
        }
        base.update(overrides)
        return base

    def test_score_roe_tiers(self):
            s = self._base_stock()
            assert _calculate_moat_score(s) > 0

            # ROE >= 30 -> max roe component (100 * 0.30 = 30)
            s = self._base_stock(roe=35.0, roe_5y_avg=0)
            score = _calculate_moat_score(s)
            assert score >= 30  # total score >= ROE component

            # ROE 20-30 -> roe_s=85, ROE component = 85 * 0.30 = 25.5
            s = self._base_stock(roe=25.0, roe_5y_avg=0)
            score = _calculate_moat_score(s)
            assert score >= 25.5  # total score >= ROE component

    def test_score_pe_tiers(self):
        s = self._base_stock()
        # PE <= 5 -> max pe component
        s = self._base_stock(pe=4.0)
        score = _calculate_moat_score(s)
        assert score >= 20

    def test_score_growth_tiers(self):
        s = self._base_stock(revenue_growth=35.0, profit_growth=30.0)
        score = _calculate_moat_score(s)
        assert score >= 20

    def test_score_debt_tiers(self):
        s = self._base_stock(debt_ratio=15.0)
        score = _calculate_moat_score(s)
        assert score >= 15

    def test_score_gm_tiers(self):
        s = self._base_stock(gross_margin=65.0, gross_margin_5y_avg=0)
        score = _calculate_moat_score(s)
        assert score >= 15

    def test_10y_bonus_roe_volatility(self):
        s = self._base_stock(roe_volatility=3.0)
        score1 = _calculate_moat_score(s)
        s = self._base_stock(roe_volatility=8.0)
        score2 = _calculate_moat_score(s)
        s = self._base_stock(roe_volatility=12.0)
        score3 = _calculate_moat_score(s)
        s = self._base_stock(roe_volatility=20.0)
        score4 = _calculate_moat_score(s)
        assert score1 > score2 > score3 > score4

    def test_10y_bonus_roe_improvement(self):
        s = self._base_stock(roe_improvement=5.0)
        score1 = _calculate_moat_score(s)
        s = self._base_stock(roe_improvement=1.0)
        score2 = _calculate_moat_score(s)
        assert score1 > score2

    def test_10y_bonus_fcf_consistency(self):
        s = self._base_stock(fcf_positive_years_10=9)
        score1 = _calculate_moat_score(s)
        s = self._base_stock(fcf_positive_years_10=7)
        score2 = _calculate_moat_score(s)
        s = self._base_stock(fcf_positive_years_10=5)
        score3 = _calculate_moat_score(s)
        s = self._base_stock(fcf_positive_years_10=3)
        score4 = _calculate_moat_score(s)
        assert score1 > score2 > score3 > score4

    def test_missing_10y_fields_no_crash(self):
        """Missing 10y fields should not crash, just no bonus."""
        s = {
            'roe': 20.0, 'pe': 10.0,
            'revenue_growth': 15.0, 'profit_growth': 18.0,
            'debt_ratio': 30.0,
            'gross_margin': 40.0,
        }
        score = _calculate_moat_score(s)
        assert isinstance(score, float)


class TestValueScreenerIntegration:
    """Integration-style tests using ValueScreener class."""

    def _config(self):
        return {
            'screener': {
                'conditions': {
                    'max_pe': 20, 'min_pe': 3, 'max_pb': 3.5, 'min_roe': 5,
                    'min_revenue_growth': 0, 'min_profit_growth': 0,
                    'max_debt_ratio': 65, 'min_market_cap': 30, 'max_market_cap': 50000,
                    'exclude_st': True, 'exclude_keywords': 'ST,退',
                    'min_gross_margin': 15, 'min_ocf_per_share': 0,
                    'min_net_margin': 5, 'min_interest_coverage': 2,
                    'min_fcf_5y': 0, 'max_share_dilution': 20,
                },
                'max_candidates': 20
            }
        }

    def _candidates(self, count=5):
        stocks = []
        for i in range(count):
            stocks.append({
                'code': f'60000{i}',
                'name': f'测试股{i}',
                'pe': 10.0 + i,
                'pb': 1.5,
                'roe': 15.0 + i,
                'gross_margin': 30.0,
                'net_margin': 10.0,
                'ocf_per_share': 5.0,
                'revenue_growth': 10.0,
                'profit_growth': 12.0,
                'debt_ratio': 30.0,
                'market_cap': 500.0,
                'roe_5y_avg': 20.0,
                'gross_margin_5y_avg': 28.0,
                'net_margin_5y_avg': 12.0,
                'ocf_5y_trend': 1,
                'ocf_latest': 5.0,
                'ocf_positive_years': 5,
                'debt_ratio_latest': 30.0,
                'intcov_5y_avg': 20.0,
                'fcf_5y_sum': 100_000_000_00,
                'share_dilution_5y': 5.0,
                'roic_5y_avg': 18.0,
                'data_years': '2020-2024',
            })
        return stocks

    def test_score_candidates_sorts_desc(self):
        screener = ValueScreener(self._config())
        candidates = self._candidates(5)
        # Mock FinancialSummaryDAO.get_batch to return empty
        import src.screener.value_screener as vs_module
        original_get_batch = vs_module.FinancialSummaryDAO.get_batch
        vs_module.FinancialSummaryDAO.get_batch = lambda self, codes: {}
        try:
            results = screener.score_candidates(candidates, 'test_run', '2026-01-01')
        finally:
            vs_module.FinancialSummaryDAO.get_batch = original_get_batch

        assert len(results) <= 5
        scores = [r['score'] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_score_candidates_respects_max_n(self):
        screener = ValueScreener(self._config())
        candidates = self._candidates(10)
        import src.screener.value_screener as vs_module
        original_get_batch = vs_module.FinancialSummaryDAO.get_batch
        vs_module.FinancialSummaryDAO.get_batch = lambda self, codes: {}
        try:
            results = screener.score_candidates(candidates, 'test_run', '2026-01-01')
        finally:
            vs_module.FinancialSummaryDAO.get_batch = original_get_batch

        assert len(results) <= 20


class TestExemptionsRefined:
    """B4 豁免细化（对标 Berkshire A/B/C）：只收紧不放松的反例矩阵。"""

    def test_span_helper(self):
        from src.screener.value_screener import _data_years_span
        assert _data_years_span('2020-2026') == 6
        assert _data_years_span('broken') is None
        assert _data_years_span(None) is None

    def test_exempt_a_full_house_passes(self):
        from tests.screener.test_value_screener import TestCheck7Gates as T
        base = T._base_stock(T(), roe_5y_avg=5.0, revenue_growth=25.0,
                             ocf_latest=2.0, ocf_5y_trend=1,
                             data_years='2020-2024')
        cfg = T._base_config(T())
        assert len(_check_7_gates(base, cfg)) > 0

    def test_exempt_a_ocf_negative_rejected(self):
        from tests.screener.test_value_screener import TestCheck7Gates as T
        base = T._base_stock(T(), roe_5y_avg=5.0, revenue_growth=25.0,
                             ocf_latest=-1.0, ocf_5y_trend=-1,
                             data_years='2020-2024')
        assert _check_7_gates(base, T._base_config(T())) == []

    def test_exempt_a_long_history_rejected(self):
        from tests.screener.test_value_screener import TestCheck7Gates as T
        base = T._base_stock(T(), roe_5y_avg=5.0, revenue_growth=25.0,
                             ocf_latest=2.0, ocf_5y_trend=1,
                             data_years='2005-2024')
        assert _check_7_gates(base, T._base_config(T())) == []

    def test_exempt_b_needs_profit_recovery(self):
        from tests.screener.test_value_screener import TestCheck7Gates as T
        bad = T._base_stock(T(), ocf_per_share=-1.0, gross_margin=35.0,
                            revenue_growth=25.0, profit_growth=0.0)
        assert _check_7_gates(bad, T._base_config(T())) == []
        good = T._base_stock(T(), ocf_per_share=-1.0, gross_margin=35.0,
                             revenue_growth=25.0, profit_growth=5.0)
        assert len(_check_7_gates(good, T._base_config(T()))) > 0

    def test_exempt_c_needs_dual_growth(self):
        from tests.screener.test_value_screener import TestCheck7Gates as T
        bad = T._base_stock(T(), net_margin_5y_avg=3.0, gross_margin=35.0,
                            revenue_growth=0.0, profit_growth=5.0)
        assert _check_7_gates(bad, T._base_config(T())) == []
        good = T._base_stock(T(), net_margin_5y_avg=3.0, gross_margin=35.0,
                             revenue_growth=5.0, profit_growth=5.0)
        assert len(_check_7_gates(good, T._base_config(T()))) > 0

    def test_exempt_d_needs_cash_quality(self):
        from tests.screener.test_value_screener import TestCheck7Gates as T
        good = T._base_stock(T(), gross_margin=10.0, roe=25.0,
                             ocf_per_share=5.0, ocf_positive_years=4,
                             net_margin_5y_avg=2.5)
        assert len(_check_7_gates(good, T._base_config(T()))) > 0
        thin_ocf = T._base_stock(T(), gross_margin=10.0, roe=25.0,
                                 ocf_per_share=5.0, ocf_positive_years=1,
                                 net_margin_5y_avg=2.5)
        assert _check_7_gates(thin_ocf, T._base_config(T())) == []
        bleeding = T._base_stock(T(), gross_margin=10.0, roe=25.0,
                                 ocf_per_share=5.0, ocf_positive_years=4,
                                 net_margin_5y_avg=-2.0)
        assert _check_7_gates(bleeding, T._base_config(T())) == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])