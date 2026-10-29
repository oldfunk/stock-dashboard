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


def test_fetch_kline_data_fallback_tx_passes_adjust():
    """Issue #9 T1：EM 失败回退腾讯时，adjust 必须透传（不能静默丢参）。

    U9-A 后 qfq 回退链变为腾讯 双拉（''+hfq）走三列模型——透传保证由
    这两脚的 adjust 实收证明（T1 的形参仍被真实使用，非静默丢弃）。
    """
    fake_tx_df = pd.DataFrame([
        {"date": "2025-07-21", "open": 18.0, "close": 18.5,
         "high": 18.8, "low": 17.9, "amount": 282482.0},
    ])

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.side_effect = Exception("Connection aborted")
        mock_ak.stock_zh_a_hist_tx.return_value = fake_tx_df
        from src.collector.akshare_fetcher import fetch_kline_data
        fetch_kline_data("000792", adjust="qfq")

    adjusts = [c.kwargs.get("adjust", c[1].get("adjust"))
               for c in mock_ak.stock_zh_a_hist_tx.call_args_list]
    assert adjusts == ["", "hfq"], f"腾讯双拉 adjust 未按契约透传: {adjusts}"


# ── U9-A：qfq 路径双拉三列模型（Issue #9 方案 A，2026-10-29）──

_RAW_DF = pd.DataFrame([
    {"日期": "2025-07-21", "开盘": 19.5, "收盘": 20.0, "最高": 20.2,
     "最低": 19.3, "成交量": 100, "成交额": 1000.0, "换手率": 0.5},
    {"日期": "2025-07-22", "开盘": 21.5, "收盘": 22.0, "最高": 22.3,
     "最低": 21.1, "成交量": 200, "成交额": 2000.0, "换手率": 0.8},
])
_HFQ_DF = pd.DataFrame([
    {"日期": "2025-07-21", "开盘": 136.5, "收盘": 140.0, "最高": 141.4,
     "最低": 135.1, "成交量": 100, "成交额": 1000.0, "换手率": 0.5},
    {"日期": "2025-07-22", "开盘": 150.5, "收盘": 176.0, "最高": 178.4,
     "最低": 147.7, "成交量": 200, "成交额": 2000.0, "换手率": 0.8},
])


def test_qfq_returns_three_column_model_em():
    """qfq 默认口径：EM 双拉（''+hfq）→ 三列模型；锚点日 close==close_raw。"""
    def hist_by_adjust(symbol=None, period=None, start_date=None,
                       end_date=None, adjust=None, **kw):
        return {"": _RAW_DF, "hfq": _HFQ_DF}.get(adjust, pd.DataFrame())

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.side_effect = hist_by_adjust
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792", start_date="2025-07-21")

    adjusts = [c.kwargs.get("adjust") for c in mock_ak.stock_zh_a_hist.call_args_list]
    assert adjusts == ["", "hfq"]
    assert len(records) == 2
    d1, d2 = records[0], records[1]
    # 锚点日（最新交集日）：close = close_raw（scale 锚定）
    assert d2["close"] == d2["close_raw"] == 22.0
    assert d2["close_adj"] == 176.0
    # 历史日：hfq×scale = 前复权；raw/adj 各归其列
    assert d1["close_raw"] == 20.0
    assert d1["close_adj"] == 140.0
    assert d1["close"] == pytest.approx(17.5)
    # 量额取 raw 侧
    assert d1["volume"] == 100
    assert d1["turnover"] == 0.5


def test_qfq_em_fail_falls_to_tx_pair():
    """EM 双拉失败 → 腾讯双拉（''+hfq）同模型；不再有单列 qfq 写出路径。"""
    tx_raw = pd.DataFrame([
        {"date": "2025-07-22", "open": 21.5, "close": 22.0,
         "high": 22.3, "low": 21.1, "amount": 2000.0},
    ])
    tx_hfq = pd.DataFrame([
        {"date": "2025-07-22", "open": 150.5, "close": 176.0,
         "high": 178.4, "low": 147.7, "amount": 2000.0},
    ])

    def tx_by_adjust(symbol=None, start_date=None, end_date=None,
                     adjust=None, **kw):
        return {"": tx_raw, "hfq": tx_hfq}.get(adjust, pd.DataFrame())

    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.side_effect = Exception("rate limited")
        mock_ak.stock_zh_a_hist_tx.side_effect = tx_by_adjust
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792", start_date="2025-07-22")

    adjusts = [c.kwargs.get("adjust") for c in mock_ak.stock_zh_a_hist_tx.call_args_list]
    assert adjusts == ["", "hfq"]
    assert len(records) == 1
    assert records[0]["close"] == records[0]["close_raw"] == 22.0
    assert records[0]["close_adj"] == 176.0
    # 腾讯无量：merge 取 raw 侧 .get → NULL（与既有回退一致）
    assert records[0]["volume"] is None


def test_qfq_both_sources_fail_returns_empty_fail_closed():
    """两源双拉全失败 → []：宁可不增量，不写单列残行（B3 coverage 教训）。"""
    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.side_effect = Exception("down")
        mock_ak.stock_zh_a_hist_tx.return_value = pd.DataFrame()
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792", adjust="qfq")
    assert records == []


def test_non_qfq_adjust_keeps_single_pull():
    """非 qfq 明确口径调用（研究/工具侧）：保持单拉，不擅自双拉改写记录结构。"""
    fake_df = pd.DataFrame([
        {"日期": "2025-07-22", "开盘": 21.5, "收盘": 22.0, "最高": 22.3,
         "最低": 21.1, "成交量": 200, "成交额": 2000.0, "换手率": 0.8},
    ])
    with patch("src.collector.akshare_fetcher.ak") as mock_ak:
        mock_ak.stock_zh_a_hist.return_value = fake_df
        from src.collector.akshare_fetcher import fetch_kline_data
        records = fetch_kline_data("000792", adjust="hfq")

    assert mock_ak.stock_zh_a_hist.call_count == 1
    assert mock_ak.stock_zh_a_hist.call_args.kwargs.get("adjust") == "hfq"
    assert "close_raw" not in records[0]
    assert "close_adj" not in records[0]
