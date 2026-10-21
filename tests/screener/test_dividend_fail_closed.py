"""红利策略 fail-closed 单测（2026-10-21）。

背景：以前缺失股息率/分红率会“跳过阈值”，导致没有真实股息数据的股票
也能进红利池。现在这两个定义性字段缺数据必须直接不命中。
"""

from src.screener.value_screener import _check_strategy_thresholds


def _stock(**kw):
    base = {
        "code": "600519",
        "name": "测试",
        "roe_5y_avg": 20.0,
        "pe": 10.0,
        "gross_margin_5y_avg": 30.0,
        "net_margin_5y_avg": 12.0,
        "debt_ratio": 30.0,
        "roe_volatility": 5.0,
        "dividend_yield": 5.0,
        "payout_ratio": 50.0,
        "fcf_5y_sum": 100_000_000_00,   # 100 亿
        "market_cap": 1000.0,           # 1000 亿 → 年化 FCF 收益率 2%
    }
    base.update(kw)
    return base


DIVIDEND = {
    "roe_avg_5y_min": 12,
    "pe_max": 20,
    "gross_margin_min": 15,
    "net_margin_min": 5,
    "debt_ratio_max": 50,
    "dividend_yield_min": 4,
    "payout_ratio_max": 70,
    "fcf_yield_min": 0.02,
    "roe_volatility_max": 10,
}


class TestDividendFailClosed:
    def test_full_data_hits(self):
        reasons = _check_strategy_thresholds(_stock(), DIVIDEND)
        assert reasons
        assert any("股息率" in r for r in reasons)
        assert any("分红率" in r for r in reasons)

    def test_missing_dividend_yield_is_excluded(self):
        s = _stock()
        s.pop("dividend_yield")
        assert _check_strategy_thresholds(s, DIVIDEND) == []

    def test_none_dividend_yield_is_excluded(self):
        assert _check_strategy_thresholds(
            _stock(dividend_yield=None), DIVIDEND) == []

    def test_missing_payout_ratio_is_excluded(self):
        s = _stock()
        s.pop("payout_ratio")
        assert _check_strategy_thresholds(s, DIVIDEND) == []

    def test_payout_too_high_is_excluded(self):
        assert _check_strategy_thresholds(
            _stock(payout_ratio=85.0), DIVIDEND) == []

    def test_dividend_yield_below_threshold_is_excluded(self):
        assert _check_strategy_thresholds(
            _stock(dividend_yield=3.0), DIVIDEND) == []


class TestOtherStrategiesStillSkipMissing:
    def test_growth_without_dividend_fields_still_hits(self):
        thresholds = {
            "roe_avg_5y_min": 15,
            "pe_max": 50,
            "gross_margin_min": 25,
            "net_margin_min": 10,
            "debt_ratio_max": 60,
            "fcf_yield_min": 0.03,
        }
        s = _stock(roe_5y_avg=25.0, pe=20.0, gross_margin_5y_avg=40.0,
                   net_margin_5y_avg=15.0, debt_ratio=30.0,
                   fcf_5y_sum=150_000_000_00)
        s.pop("dividend_yield")
        s.pop("payout_ratio")
        assert _check_strategy_thresholds(s, thresholds)

    def test_missing_fcf_still_skips(self):
        thresholds = {
            "roe_avg_5y_min": 15,
            "pe_max": 50,
            "gross_margin_min": 25,
            "net_margin_min": 10,
            "debt_ratio_max": 60,
            "fcf_yield_min": 0.03,
        }
        s = _stock(roe_5y_avg=25.0, pe=20.0, gross_margin_5y_avg=40.0,
                   net_margin_5y_avg=15.0, debt_ratio=30.0)
        s.pop("fcf_5y_sum")
        assert _check_strategy_thresholds(s, thresholds)
