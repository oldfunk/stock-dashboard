"""B1 估值验算闸纯函数单测（无 DB 依赖）。"""

import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, 'scripts'))

from verify_valuation import (  # noqa: E402
    deviation_pct, exact, verdict_for, verify_market_cap, verify_ratios,
    cross_check,
)


class TestExact:
    def test_float_trap_avoided(self):
        assert exact(0.1) + exact(0.2) == exact('0.3')

    def test_zero_reported_skips(self):
        assert deviation_pct(exact(100), exact(0)) is None
        assert verdict_for(None) == 'SKIP'


class TestThresholds:
    def test_pass_within_1pct(self):
        assert verdict_for(0.5) == 'PASS'

    def test_warn_between_1_and_5(self):
        assert verdict_for(3.0) == 'WARN'

    def test_fail_above_5(self):
        assert verdict_for(5.01) == 'FAIL'


class TestMarketCap:
    def test_maotai_scale(self):
        # 茅台量级：1500 元 × 12.56 亿股 = 18840 亿
        r = verify_market_cap(1500.0, 1.256e9, 18840.0)
        assert r['verdict'] == 'PASS'
        assert abs(r['calculated_yi'] - 18840.0) < 1.0

    def test_unit_mismatch_caught(self):
        # 报告市值少个零（100 亿写成 10 亿）→ 失败
        r = verify_market_cap(10.0, 1e9, 10.0)
        assert r['verdict'] == 'FAIL'

    def test_missing_shares_skip_not_fail(self):
        r = verify_market_cap(10.0, None, 100.0)
        assert r['verdict'] == 'SKIP'


class TestRatios:
    def test_pe_recompute(self):
        out = verify_ratios(20.0, 1.0, None, 20.0, None)
        assert out['pe']['verdict'] == 'PASS'
        assert out['pe']['calculated'] == 20.0

    def test_pe_divergence_caught(self):
        out = verify_ratios(20.0, 1.0, None, 40.0, None)
        assert out['pe']['verdict'] == 'FAIL'


class TestCrossCheck:
    def test_copy_consistent(self):
        r = cross_check('pe', 15.5, 15.5)
        assert r['verdict'] == 'PASS'

    def test_one_side_missing(self):
        r = cross_check('pe', 15.5, None)
        assert r['verdict'] == 'SKIP'


class TestCirculating:
    """V1b 流通口径：流通市值/现价 vs 年报总股本，紧阈值。"""

    def test_diantou_self_consistent(self):
        # 电投能源：流通 655.66亿 / 29.25 ≈ 22.41亿股 ≈ 年报 22.39亿 → 通过
        r = verify_market_cap(29.25, 2.239e9, 655.66)
        assert r['verdict'] == 'PASS'

    def test_total_vs_circulating_flagged(self):
        # 误拿总市值 914.49 当流通口径 → 28% 偏差 → 失败（正是 V1b 要抓的）
        r = verify_market_cap(29.25, 2.239e9, 914.49)
        assert r['verdict'] == 'FAIL'
