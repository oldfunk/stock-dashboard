"""fetch_kline_data 单元测试（mock akshare）"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


def test_fetch_kline_data_basic():
    """基本拉取：东方财富成功，返回正确字段"""
    fake_df = pd.DataFrame([
        {"日期": "2025-07-21", "开盘": 18.0, "收盘": 18.5, "最高": 18.8,
         "最低": 17.9, "成交量": 12345600, "成交额": 2.3e8, "换手率": 1.2},
        {"日期": "2025-07-22", "开盘": 18.5, "收盘": 19.0, "最高": 19.2,
         "最低": 18.3, "成交量": 9876500, "成交额": 1.9e8, "换手率": 0.9},
    ])

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = fake_df
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792")

    assert len(records) == 2
    assert records[0]["trade_date"] == "2025-07-21"
    assert records[0]["open"] == 18.0
    assert records[0]["close"] == 18.5
    assert records[0]["volume"] == 12345600
    assert records[0]["turnover"] == 1.2


def test_fetch_kline_data_empty():
    """东方财富返回空，腾讯也返回空 → 空列表"""
    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
        mock_ak.stock_zh_a_hist_tx.return_value = pd.DataFrame()
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792")
    assert records == []


def test_fetch_kline_data_none():
    """东方财富返回 None，腾讯也返回 None → 空列表"""
    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = None
        mock_ak.stock_zh_a_hist_tx.return_value = None
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792")
    assert records == []


def test_fetch_kline_data_date_conversion():
    """start_date 的 YYYY-MM-DD 应转为 akshare 的 YYYYMMDD"""
    fake_df = pd.DataFrame([
        {"日期": "2025-07-21", "开盘": 18.0, "收盘": 18.5, "最高": 18.8,
         "最低": 17.9, "成交量": 100, "成交额": 1000.0, "换手率": 0.1},
    ])

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = fake_df
        from src.collector.akshare_fetcher import fetch_kline_data
        fetch_kline_data("000792", start_date="2025-01-01")

    # 验证传给东方财富的 start_date 是 YYYYMMDD 格式
    call_kwargs = mock_ak.stock_zh_a_hist.call_args
    assert call_kwargs.kwargs.get("start_date") == "20250101" or \
           call_kwargs[1].get("start_date") == "20250101"


def test_fetch_kline_data_fallback_to_tx():
    """东方财富失败时回退腾讯数据源"""
    fake_tx_df = pd.DataFrame([
        {"date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "amount": 282482.0},
        {"date": "2025-07-22", "open": 18.5, "close": 19.0,
         "high": 19.2, "low": 18.3, "amount": 532577.0},
    ])

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        # 东方财富抛异常 → 回退腾讯
        mock_ak.stock_zh_a_hist.side_effect = Exception("Connection aborted")
        mock_ak.stock_zh_a_hist_tx.return_value = fake_tx_df
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792")

    assert len(records) == 2
    assert records[0]["trade_date"] == "2025-07-21"
    assert records[0]["open"] == 18.0
    assert records[0]["close"] == 18.5
    # 腾讯数据源无成交量和换手率
    assert records[0]["volume"] is None
    assert records[0]["turnover"] is None
    assert records[0]["amount"] == 282482.0
    # 验证腾讯用 sz000792 格式
    tx_call = mock_ak.stock_zh_a_hist_tx.call_args
    assert tx_call.kwargs.get("symbol") == "sz000792" or \
           tx_call[1].get("symbol") == "sz000792"


def test_fetch_kline_data_fallback_tx_sh_code():
    """上交所股票回退腾讯时用 sh 前缀"""
    fake_tx_df = pd.DataFrame([
        {"date": "2025-07-21", "open": 1680.0, "close": 1690.0,
         "high": 1695.0, "low": 1675.0, "amount": 1000000.0},
    ])

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.side_effect = Exception("Connection aborted")
        mock_ak.stock_zh_a_hist_tx.return_value = fake_tx_df
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("600519")

    assert len(records) == 1
    tx_call = mock_ak.stock_zh_a_hist_tx.call_args
    assert tx_call.kwargs.get("symbol") == "sh600519" or \
           tx_call[1].get("symbol") == "sh600519"
