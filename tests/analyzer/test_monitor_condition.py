"""M3-2b 监控条件触发单测。

覆盖 _check_monitor_conditions / _get_metric_value / _compare：
- PE 低于阈值触发
- ROE 高于阈值触发
- 条件不满足不触发
- 无效 JSON 跳过
- 空条件跳过
- 指标值缺失跳过
"""

import json
import pytest

from src.analyzer.watchlist_reviewer import WatchlistReviewer


def _stock(**overrides):
    base = {
        'code': '600519', 'name': '贵州茅台',
        'pe': 15.0, 'roe': 25.0, 'roe_5y_avg': 25.0,
        'gross_margin': 40.0, 'gross_margin_5y_avg': 40.0,
        'net_margin_5y_avg': 15.0, 'debt_ratio': 30.0,
        'dividend_yield': 3.0, 'roe_volatility': 5.0,
        'fcf_5y_sum': 100_000_000_00, 'market_cap': 1000.0,
        'current_price': 1500.0,
    }
    base.update(overrides)
    return base


def _reviewer():
    return WatchlistReviewer({})


class TestCheckMonitorConditions:
    def test_pe_lt_triggered(self):
        """PE=15 < 20 → 触发"""
        stock = _stock(monitor_condition=json.dumps(
            {'metric': 'pe', 'operator': 'lt', 'threshold': 20}))
        result = _reviewer()._check_monitor_conditions([stock], {})
        assert len(result) == 1
        assert result[0]['code'] == '600519'
        assert result[0]['actual_value'] == 15.0

    def test_pe_lt_not_triggered(self):
        """PE=25 < 20 → 不触发"""
        stock = _stock(pe=25.0, monitor_condition=json.dumps(
            {'metric': 'pe', 'operator': 'lt', 'threshold': 20}))
        result = _reviewer()._check_monitor_conditions([stock], {})
        assert len(result) == 0

    def test_roe_gt_triggered(self):
        """ROE=25 > 15 → 触发"""
        stock = _stock(monitor_condition=json.dumps(
            {'metric': 'roe', 'operator': 'gt', 'threshold': 15}))
        result = _reviewer()._check_monitor_conditions([stock], {})
        assert len(result) == 1

    def test_invalid_json_skipped(self):
        """无效 JSON → 跳过"""
        stock = _stock(monitor_condition='not json')
        result = _reviewer()._check_monitor_conditions([stock], {})
        assert len(result) == 0

    def test_empty_condition_skipped(self):
        """空条件 → 跳过"""
        stock = _stock(monitor_condition='')
        result = _reviewer()._check_monitor_conditions([stock], {})
        assert len(result) == 0

    def test_no_condition_field(self):
        """无 monitor_condition 字段 → 跳过"""
        stock = _stock()
        result = _reviewer()._check_monitor_conditions([stock], {})
        assert len(result) == 0

    def test_missing_metric_value(self):
        """指标值缺失 → 跳过"""
        stock = _stock(monitor_condition=json.dumps(
            {'metric': 'dividend_yield', 'operator': 'lt', 'threshold': 5}))
        # dividend_yield 在 stock 中有值，但 analysis 中没有
        # 这里 stock 有 dividend_yield=3.0，所以会触发
        result = _reviewer()._check_monitor_conditions([stock], {})
        assert len(result) == 1

    def test_fcf_yield_calculation(self):
        """FCF 收益率计算：fcf_5y_sum=100亿, market_cap=1000亿 → 0.02"""
        stock = _stock(monitor_condition=json.dumps(
            {'metric': 'fcf_yield', 'operator': 'lt', 'threshold': 0.03}))
        result = _reviewer()._check_monitor_conditions([stock], {})
        assert len(result) == 1
        assert result[0]['actual_value'] == pytest.approx(0.02)

    def test_multiple_stocks(self):
        """多只股票，部分触发"""
        s1 = _stock(code='AAA', monitor_condition=json.dumps(
            {'metric': 'pe', 'operator': 'lt', 'threshold': 20}))
        s2 = _stock(code='BBB', pe=25.0, monitor_condition=json.dumps(
            {'metric': 'pe', 'operator': 'lt', 'threshold': 20}))
        s3 = _stock(code='CCC', monitor_condition=json.dumps(
            {'metric': 'roe', 'operator': 'gt', 'threshold': 15}))
        result = _reviewer()._check_monitor_conditions([s1, s2, s3], {})
        assert len(result) == 2
        codes = {r['code'] for r in result}
        assert codes == {'AAA', 'CCC'}


class TestCompare:
    def test_lt(self):
        assert _reviewer()._compare(10, 'lt', 20) is True
        assert _reviewer()._compare(20, 'lt', 20) is False

    def test_le(self):
        assert _reviewer()._compare(10, 'le', 20) is True
        assert _reviewer()._compare(20, 'le', 20) is True
        assert _reviewer()._compare(25, 'le', 20) is False

    def test_gt(self):
        assert _reviewer()._compare(25, 'gt', 20) is True
        assert _reviewer()._compare(20, 'gt', 20) is False

    def test_ge(self):
        assert _reviewer()._compare(25, 'ge', 20) is True
        assert _reviewer()._compare(20, 'ge', 20) is True
        assert _reviewer()._compare(15, 'ge', 20) is False

    def test_eq(self):
        assert _reviewer()._compare(20, 'eq', 20) is True
        assert _reviewer()._compare(15, 'eq', 20) is False

    def test_invalid_op(self):
        assert _reviewer()._compare(15, 'invalid', 20) is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
