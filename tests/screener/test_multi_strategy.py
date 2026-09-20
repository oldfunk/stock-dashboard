"""多策略筛选模式单测：三策略独立候选池 + strategy_tags 落库。

覆盖任务 M2-2：
- test_multi_strategy_screening：score_candidates(multi_strategy=True)
  按 strategies.yaml 三策略阈值输出独立 Top-N 池，strategy_tags 为全量命中标签
- test_strategy_tags_persistence：strategy_tags 经 save_batch →
  get_results_for_run 往返不丢失；缺 key 的老行写入为 NULL（向后兼容）
- test_load_strategies_reads_yaml：load_strategies() 读真实配置文件
"""

import json
import sqlite3

import pytest

import src.models.database as db_mod
from src.models.database import ScreeningResultDAO, init_database
from src.screener.value_screener import (
    ValueScreener,
    load_strategies,
)


def _base_stock(**overrides):
    base = {
        'code': '600519',
        'name': '贵州茅台',
        'is_st': False,
        'pe': 15.0,
        'pb': 2.0,
        'roe': 10.0,
        'roe_5y_avg': 25.0,
        'gross_margin': 40.0,
        'gross_margin_5y_avg': 40.0,
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


def _base_config():
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


def _demo_strategies():
    """与 config/strategies.yaml 同口径的注入式策略（单测不依赖文件内容漂移）。

    注意：扁平结构，阈值直接在策略根下，无 thresholds 子键。
    """
    return {
        'growth': {
            'roe_avg_5y_min': 15, 'pe_max': 50, 'gross_margin_min': 25,
            'net_margin_min': 10, 'debt_ratio_max': 60,
            'fcf_yield_min': 0.03,
        },
        'dividend': {
            'roe_avg_5y_min': 12, 'pe_max': 20, 'gross_margin_min': 15,
            'net_margin_min': 5, 'debt_ratio_max': 50,
            'dividend_yield_min': 4, 'fcf_yield_min': 0.02,
        },
        'turnaround': {
            'roe_avg_5y_min': 5, 'pe_max': 15, 'gross_margin_min': 10,
            'net_margin_min': 3, 'debt_ratio_max': 70,
            'fcf_yield_min': 0.01, 'debt_to_equity_max': 2.0,
        },
    }


class TestMultiStrategyScreening:
    def test_multi_strategy_screening(self):
        a = _base_stock(code='AAA', name='全能', fcf_5y_sum=150_000_000_00)
        b = _base_stock(code='BBB', name='红利反转',
                        gross_margin=20.0, gross_margin_5y_avg=20.0)
        c = _base_stock(code='CCC', name='高估', pe=25.0)
        sc = ValueScreener(_base_config())
        pools = sc.score_candidates(
            [a, b, c], 'r1', '2026-09-14',
            multi_strategy=True, strategies=_demo_strategies())

        assert set(pools) == {'growth', 'dividend', 'turnaround'}
        assert {r['code'] for r in pools['growth']} == {'AAA'}
        assert {r['code'] for r in pools['dividend']} == {'AAA', 'BBB'}
        assert {r['code'] for r in pools['turnaround']} == {'AAA', 'BBB'}

        tags_a = json.loads(pools['growth'][0]['strategy_tags'])
        assert tags_a == ['dividend', 'growth', 'turnaround']
        tags_b = json.loads(
            [r for r in pools['dividend'] if r['code'] == 'BBB'][0]['strategy_tags'])
        assert tags_b == ['dividend', 'turnaround']
        # 同一股票在不同池中的标签一致（全量标签）
        for pool in pools.values():
            for r in pool:
                if r['code'] == 'AAA':
                    assert json.loads(r['strategy_tags']) == tags_a

    def test_single_mode_backward_compat(self):
        """默认单策略模式：返回 list，strategy_tags 为 None（DB 存 NULL）。"""
        sc = ValueScreener(_base_config())
        out = sc.score_candidates([_base_stock()], 'r1', '2026-09-14')
        assert isinstance(out, list) and len(out) == 1
        assert out[0]['strategy_tags'] is None

    def test_load_strategies_reads_yaml(self):
        cfg = load_strategies()
        assert set(cfg) == {'growth', 'dividend', 'turnaround'}
        assert cfg['growth']['gross_margin_min'] == 25
        assert cfg['dividend']['dividend_yield_min'] == 4
        assert cfg['turnaround']['pe_max'] == 15


class TestStrategyTagsPersistence:
    def test_strategy_tags_persistence(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / 'test.db')
        monkeypatch.setattr(db_mod, 'get_db_path', lambda: db_path)
        init_database()

        cols = {r[1] for r in sqlite3.connect(db_path).execute(
            'PRAGMA table_info(screening_result)').fetchall()}
        assert 'strategy_tags' in cols

        dao = ScreeningResultDAO()
        dao.save_batch([
            {'run_id': 'r1', 'run_date': '2026-09-14', 'code': 'AAA',
             'name': '全能', 'score': 90.0,
             'strategy_tags': json.dumps(['dividend', 'growth']),
             'reason': 'x'},
            {'run_id': 'r1', 'run_date': '2026-09-14', 'code': 'BBB',
             'name': '老行', 'score': 80.0, 'reason': 'y'},  # 无 key → NULL
        ])
        rows = {r['code']: r for r in dao.get_results_for_run('r1')}
        assert json.loads(rows['AAA']['strategy_tags']) == ['dividend', 'growth']
        assert rows['BBB']['strategy_tags'] is None

    def test_migration_adds_column_to_old_table(self, tmp_path, monkeypatch):
        """旧表无 strategy_tags 列时 init_database 自动迁移补齐。"""
        db_path = str(tmp_path / 'old.db')
        monkeypatch.setattr(db_mod, 'get_db_path', lambda: db_path)
        init_database()
        con = sqlite3.connect(db_path)
        con.execute('ALTER TABLE screening_result DROP COLUMN strategy_tags')
        con.commit()
        con.close()
        init_database()
        cols = {r[1] for r in sqlite3.connect(db_path).execute(
            'PRAGMA table_info(screening_result)').fetchall()}
        assert 'strategy_tags' in cols
