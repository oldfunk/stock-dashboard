"""M3-1 论点漂移检测单测。

覆盖 check_argument_drift 函数的各种场景：
- 入池理由含正面词 + signal=AVOID → 漂移
- 入池理由含正面词 + signal=BUY → 不漂移
- 入池理由不含正面词 + signal=AVOID → 不漂移
- 入池理由为空 → 不漂移
- analysis 为 None → 不漂移
"""

import json
import pytest

from src.analyzer.watchlist_reviewer import check_argument_drift


def _stock(**overrides):
    base = {'code': '600519', 'name': '贵州茅台', 'added_reason': '低PE 高ROE 优质龙头'}
    base.update(overrides)
    return base


def _analysis(signal='AVOID'):
    return {
        'trade_strategy': json.dumps({'signal': signal, 'confidence': 'high'}),
    }


class TestArgumentDrift:
    def test_drift_positive_reason_avoid(self):
        """入池理由含正面词 + AVOID → 漂移"""
        assert check_argument_drift(_stock(), _analysis('AVOID')) is True

    def test_no_drift_buy_signal(self):
        """入池理由含正面词 + BUY → 不漂移"""
        assert check_argument_drift(_stock(), _analysis('BUY')) is False

    def test_no_drift_no_positive_keyword(self):
        """入池理由不含正面词 + AVOID → 不漂移"""
        stock = _stock(added_reason='困境反转机会')
        assert check_argument_drift(stock, _analysis('AVOID')) is False

    def test_no_drift_empty_reason(self):
        """入池理由为空 → 不漂移"""
        stock = _stock(added_reason='')
        assert check_argument_drift(stock, _analysis('AVOID')) is False

    def test_no_drift_none_analysis(self):
        """analysis 为 None → 不漂移"""
        assert check_argument_drift(_stock(), None) is False

    def test_no_drift_hold_signal(self):
        """signal=HOLD → 不漂移"""
        assert check_argument_drift(_stock(), _analysis('HOLD')) is False

    def test_drift_various_positive_keywords(self):
        """各种正面关键词都能触发漂移"""
        keywords = ['低PE', '高ROE', '低估值', '高质量', '稳定',
                    '成长', '护城河', '低估', '优质', '龙头']
        for kw in keywords:
            stock = _stock(added_reason=f'{kw} 基本面优秀')
            assert check_argument_drift(stock, _analysis('AVOID')) is True, f'{kw} 应触发漂移'

    def test_drift_case_insensitive(self):
        """signal 大小写不敏感"""
        assert check_argument_drift(_stock(), _analysis('avoid')) is True
        assert check_argument_drift(_stock(), _analysis('Avoid')) is True

    def test_drift_trade_strategy_as_dict(self):
        """trade_strategy 为 dict 而非 JSON 字符串"""
        analysis = {'trade_strategy': {'signal': 'AVOID', 'confidence': 'high'}}
        assert check_argument_drift(_stock(), analysis) is True

    def test_no_drift_invalid_json(self):
        """trade_strategy 为非法 JSON → 不漂移"""
        analysis = {'trade_strategy': 'not json'}
        assert check_argument_drift(_stock(), analysis) is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
