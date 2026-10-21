"""东财指数日线适配单测（2026-10-21）。

背景：新浪 sh000922 只更新到 2019 年，信号验证必须改用东财。
hermetic：fake ak，不联网。
"""

import pandas as pd
import pytest

from src.collector import akshare_fetcher as f


def test_fetch_index_kline_em_normalizes_and_slices(monkeypatch):
    class FakeAk:
        @staticmethod
        def stock_zh_index_daily_em(symbol):
            assert symbol == "sh000922"
            return pd.DataFrame([
                {"date": "2026-10-20", "open": 5500.0, "close": 5520.0,
                 "high": 5530.0, "low": 5490.0, "volume": 100.0,
                 "amount": 200.0},
                {"date": "2026-10-21", "open": 5520.0, "close": 5498.0,
                 "high": 5540.0, "low": 5480.0, "volume": 110.0,
                 "amount": 210.0},
                {"date": "2026-10-22", "open": 5498.0, "close": None,
                 "high": 5500.0, "low": 5400.0, "volume": 0.0,
                 "amount": 0.0},
            ])

    monkeypatch.setattr(f, "ak", FakeAk)
    rows = f.fetch_index_kline_em("sh000922", limit=2)
    assert [r["trade_date"] for r in rows] == ["2026-10-20", "2026-10-21"]
    assert rows[-1]["close"] == 5498.0
    assert rows[-1]["volume"] == 110.0
    # 无效收盘价被剔除，而不是填 0
    assert all(r["close"] > 0 for r in rows)


def test_fetch_index_kline_em_empty_on_error(monkeypatch):
    class Broken:
        @staticmethod
        def stock_zh_index_daily_em(symbol):
            raise RuntimeError("network down")

    monkeypatch.setattr(f, "ak", Broken)
    assert f.fetch_index_kline_em("sh000922") == []
