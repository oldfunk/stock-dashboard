"""B1 估值验算闸纯函数单测（无 DB 依赖）。"""

import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, 'scripts'))

from verify_valuation import (  # noqa: E402
    deviation_pct, exact, verdict_for, verify_market_cap, verify_ratios,
    cross_check, verify_run, main,
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


class TestReportedKey:
    """P0-2：verify_market_cap 必须带 reported 键（prompt 渲染快照值）。"""

    def test_normal_result_has_reported(self):
        r = verify_market_cap(1500.0, 1.256e9, 18840.0)
        assert r['reported'] == 18840.0

    def test_skip_result_has_reported(self):
        r = verify_market_cap(10.0, None, 100.0)
        assert r['reported'] == 100.0

    def test_missing_cap_reports_none(self):
        r = verify_market_cap(10.0, 1e9, None)
        assert r['verdict'] == 'SKIP'
        assert r['reported'] is None


class TestZeroSampleHonesty:
    """P0-2：双源全缺数 → verified=0、exit 2，不许以“零失败”谎报成功。"""

    @staticmethod
    def _make_db(tmp_path):
        import sqlite3
        db = str(tmp_path / 'gate.db')
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE screening_result '
                    '(run_id TEXT, run_date TEXT, code TEXT, name TEXT, '
                    'pe REAL, pb REAL, market_cap REAL)')
        con.execute("INSERT INTO screening_result VALUES "
                    "('r1','2026-09-22','600519','茅台',NULL,NULL,NULL)")
        con.execute('CREATE TABLE stock_snapshot '
                    '(code TEXT, current_price REAL, pe REAL, pb REAL, '
                    'market_cap REAL, circulating_cap REAL)')
        con.execute('CREATE TABLE financial_history '
                    '(stock_code TEXT, report_date TEXT, report_type TEXT, '
                    'eps REAL, total_shares REAL)')
        con.commit()
        con.close()
        return db

    def test_verify_run_verified_zero(self, tmp_path):
        s = verify_run(db_path=self._make_db(tmp_path),
                       report_dir=str(tmp_path))
        assert s['total'] == 1 and s['skip'] == 1
        assert s['verified'] == 0

    def test_main_exit_2_all_skip(self, tmp_path):
        db = self._make_db(tmp_path)
        code = main(['--db', db, '--report-dir', str(tmp_path), '--quiet'])
        assert code == 2
