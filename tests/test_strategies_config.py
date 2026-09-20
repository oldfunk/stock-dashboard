"""B3 strategies.yaml 三策略扁平阈值结构单测。"""

import os

import yaml

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg():
    with open(os.path.join(PROJ, 'config', 'strategies.yaml'),
              encoding='utf-8') as f:
        return yaml.safe_load(f)


class TestStrategiesConfig:
    def test_three_strategies_present(self):
        cfg = _cfg()
        assert set(cfg) == {'growth', 'dividend', 'turnaround'}

    def test_growth_thresholds(self):
        g = _cfg()['growth']
        assert g['roe_avg_5y_min'] == 15
        assert g['pe_max'] == 50
        assert g['gross_margin_min'] == 25
        assert g['net_margin_min'] == 10
        assert g['debt_ratio_max'] == 60

    def test_dividend_thresholds(self):
        d = _cfg()['dividend']
        assert d['roe_avg_5y_min'] == 12
        assert d['pe_max'] == 20
        assert d['dividend_yield_min'] == 4
        assert d['payout_ratio_max'] == 70
        assert d['fcf_yield_min'] == 0.02

    def test_turnaround_thresholds(self):
        t = _cfg()['turnaround']
        assert t['pe_max'] == 15
        assert t['debt_to_equity_max'] == 2.0
        assert t['fcf_yield_min'] == 0.01

    def test_flat_structure_no_nested_thresholds(self):
        """阈值键直接在策略根下，不在 thresholds 子键下。"""
        for key in ('growth', 'dividend', 'turnaround'):
            cfg = _cfg()[key]
            assert 'thresholds' not in cfg, f'{key} 不应有 thresholds 子键'
            # 阈值字段直接在根
            assert any(k in cfg for k in (
                'roe_avg_5y_min', 'pe_max', 'gross_margin_min',
                'dividend_yield_min', 'debt_ratio_max', 'fcf_yield_min'
            ))
