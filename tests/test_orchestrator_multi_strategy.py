"""orchestrator multi_strategy 开关单测（M2收尾）。

- test_default_is_single_strategy：config 无 screening 键时 run_screener
  以 multi_strategy=False 调用（改动前单策略行为不变回归）
- test_multi_strategy_true_two_pools：screening.multi_strategy=true 时
  以 multi_strategy=True 调用，mock 返回两池并集行验证透传
- test_is_multi_strategy_defaults：开关读取默认值/显式值
"""

import json
from unittest.mock import patch

import src.orchestrator as orch_mod
from src.orchestrator import _is_multi_strategy, run_collect_and_screen


def _candidates():
    return [
        {'code': '600519', 'name': 'A', 'roe': 20.0},
        {'code': '000001', 'name': 'B', 'roe': 18.0},
    ]


def _run_with_config(config):
    """mock 整条流水线上游，只放行到 run_screener 调用。"""
    with patch('src.collector.akshare_fetcher.run_collect_pipeline',
               return_value=_candidates()), \
        patch.object(orch_mod, 'StockSnapshotDAO') as mock_snap, \
        patch.object(orch_mod, 'PipelineProgressDAO'), \
        patch.object(orch_mod, 'RunLogDAO'), \
        patch('src.collector.akshare_fetcher.collect_historical_financial_data',
              return_value=0), \
        patch('src.collector.akshare_fetcher.rebuild_financial_summaries'), \
        patch('src.collector.akshare_fetcher.enrich_financial_data'), \
        patch('src.collector.akshare_fetcher.fetch_tencent_batch',
              return_value=[]), \
        patch('src.collector.akshare_fetcher.fetch_sector_map',
              return_value={}), \
        patch('src.models.database.WatchlistDAO'), \
        patch('src.screener.value_screener.run_screener') as mock_screen:
        mock_snap.return_value.count.return_value = 2
        result = run_collect_and_screen(config)
        return result, mock_screen


def test_default_is_single_strategy():
    result, mock_screen = _run_with_config({})
    mock_screen.assert_called_once()
    _, kwargs = mock_screen.call_args
    assert kwargs.get('multi_strategy', False) is False
    top, _, _, _ = result
    assert top == mock_screen.return_value


def test_multi_strategy_true_two_pools():
    rows = [
        {'code': '600519', 'name': 'A', 'score': 90, 'pe': 15.0,
         'strategy_tags': json.dumps(['growth', 'dividend'], ensure_ascii=False)},
        {'code': '000001', 'name': 'B', 'score': 80, 'pe': 12.0,
         'strategy_tags': json.dumps(['dividend'], ensure_ascii=False)},
    ]
    with patch('src.collector.akshare_fetcher.run_collect_pipeline',
               return_value=_candidates()), \
        patch.object(orch_mod, 'StockSnapshotDAO') as mock_snap, \
        patch.object(orch_mod, 'PipelineProgressDAO'), \
        patch.object(orch_mod, 'RunLogDAO'), \
        patch('src.collector.akshare_fetcher.collect_historical_financial_data',
              return_value=0), \
        patch('src.collector.akshare_fetcher.rebuild_financial_summaries'), \
        patch('src.collector.akshare_fetcher.enrich_financial_data'), \
        patch('src.collector.akshare_fetcher.fetch_tencent_batch',
              return_value=[]), \
        patch('src.collector.akshare_fetcher.fetch_sector_map',
              return_value={}), \
        patch('src.models.database.WatchlistDAO'), \
        patch('src.screener.value_screener.run_screener',
              return_value=rows) as mock_screen:
        mock_snap.return_value.count.return_value = 2
        top, _, _, _ = run_collect_and_screen(
            {'screening': {'multi_strategy': True}})
    mock_screen.assert_called_once()
    _, kwargs = mock_screen.call_args
    assert kwargs.get('multi_strategy') is True
    assert top == rows
    assert len(top) == 2


def test_is_multi_strategy_defaults():
    assert _is_multi_strategy({}) is False
    assert _is_multi_strategy(None) is False
    assert _is_multi_strategy({'screening': {}}) is False
    assert _is_multi_strategy({'screening': {'multi_strategy': True}}) is True
    assert _is_multi_strategy({'screening': {'multi_strategy': False}}) is False
