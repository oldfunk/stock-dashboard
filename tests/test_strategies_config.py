"""B3 strategies.yaml 成长α结构单测。"""

import os

import yaml

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _growth():
    with open(os.path.join(PROJ, 'config', 'strategies.yaml'),
              encoding='utf-8') as f:
        return yaml.safe_load(f)['growth']


class TestGrowthAlpha:
    def test_three_criteria_present(self):
        g = _growth()
        ac = g['alpha_criteria']
        assert ac['pricing_power']['gross_margin_min'] == 0.30
        assert ac['moat']['roe_avg_5y_min'] == 0.15
        assert ac['growth_quality']['ocf_per_share_min'] == 0.0

    def test_valuation_anchor_and_exits(self):
        g = _growth()
        assert g['valuation_anchor']['bubble_pe'] == 40.0
        assert len(g['exit_triggers']) >= 3

    def test_other_strategies_intact(self):
        with open(os.path.join(PROJ, 'config', 'strategies.yaml'),
                  encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        assert cfg['dividend']['thresholds']['dividend_yield_min'] == 0.04
        assert cfg['turnaround']['thresholds']['pe_max'] == 15.0
