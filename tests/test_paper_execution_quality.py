"""执行质量（E2）纯函数：VWAP 偏离、区间位置、费用汇总。"""
from __future__ import annotations

import pytest

from scripts.paper_execution_quality import execution_row, summarize


def test_execution_row_computes_vwap_and_close():
    fill = {"timestamp": "2026-11-27T09:30:34", "symbol": "848",
            "direction": 1, "volume": 100, "price": 7.40,
            "commission": 5.0, "stamp_duty": 0.0, "transfer_fee": 0.0074}
    bar = {"high": 7.6, "low": 7.2, "close": 7.5,
           "amount": 740.0 * 1000, "volume": 1000}
    row = execution_row(fill, bar)
    assert row["symbol"] == "000848"
    assert row["direction"] == "买入"
    assert row["vwap"] == pytest.approx(740.0)
    assert row["dev_bps"] == pytest.approx(-9900.0, abs=1)
    assert row["range_pos"] == pytest.approx(0.5)
    assert row["dev_close_bps"] == pytest.approx(
        (7.4 / 7.5 - 1) * 10000, abs=1)
    assert row["fee"] == pytest.approx(5.0074)


def test_execution_row_without_bar_keeps_null_metrics():
    row = execution_row({"timestamp": "2026-10-08T17:17:15",
                         "symbol": "000792", "direction": -1,
                         "volume": 400, "price": 23.97}, None)
    assert row["direction"] == "卖出"
    assert row["vwap"] is None and row["dev_bps"] is None
    assert row["close"] is None


def test_summarize_groups_by_direction():
    rows = [{"direction": "买入", "fee": 5.0, "dev_bps": -10.0,
             "dev_close_bps": 5.0},
            {"direction": "卖出", "fee": 6.0, "dev_bps": 20.0,
             "dev_close_bps": -3.0}]
    s = summarize(rows)
    assert s["n"] == 2 and s["fees"] == 11.0
    assert s["avg_dev_bps"] == pytest.approx(5.0)
    assert s["by_direction"]["买入"]["avg_dev_bps"] == -10.0
    assert s["by_direction"]["卖出"]["n"] == 1
