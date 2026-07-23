"""fetch_kline_data 单元测试（mock akshare）"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


def test_fetch_kline_data_basic():
    """基本拉取：返回正确字段"""
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
    """akshare 返回空 DataFrame"""
    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792")
    assert records == []


def test_fetch_kline_data_none():
    """akshare 返回 None"""
    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = None
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

    # 验证传给 akshare 的 start_date 是 YYYYMMDD 格式
    call_kwargs = mock_ak.stock_zh_a_hist.call_args
    assert call_kwargs.kwargs.get("start_date") == "20250101" or \
           call_kwargs[1].get("start_date") == "20250101"
