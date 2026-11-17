"""T75（Hermes T74 残留两项）：1d 聚合的 prev_close 回退 + 交易所前缀覆盖。

两项都是"今天不出事、改天必出事"的那类：
* 日线 raw 缺某天的 close ⇒ 聚合出的"当日 OHLC" prev_close/带是 NaN ⇒
  涨跌停判断拿 NaN 比（`matcher/base.py:207-218` 认的就是这两列）；
* 代码→交易所映射只判首字符 ⇒ 北交所 `920156` 拼成 `920156.XSHG`，
  rqalpha 标的表查不到 ⇒ 那只标的在框架里"不存在"，持仓/意图都无声消失。
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_trading.integration.rq_engine.bands import limit_prices   # noqa: E402
from paper_trading.integration.rq_engine.runner import (            # noqa: E402
    build_config, init_positions_arg, reconcile_init_positions)
from paper_trading.integration.rq_engine.store import (             # noqa: E402
    BAR_DTYPE_MINUTE, aggregate_day_from_minutes, to_order_book_id)
from paper_trading.utils import markets                              # noqa: E402

FEE = {"commission_rate": 0.00025, "commission_min": 5.0,
       "stamp_duty_rate": 0.0005, "transfer_fee_rate": 0.00001}


def _bars(days_with_prices):
    """[[(day, [(t, open, high, low, close, prev_close, limit_up)])…]] → 结构化数组。"""
    rows = []
    for day, mins in days_with_prices:
        for (hhmm, o, h, l, c, pc, lu) in mins:
            r = np.zeros(1, dtype=BAR_DTYPE_MINUTE)[0]
            r["datetime"] = int(day.replace("-", "")) * 1_000_000 + int(hhmm) * 100
            r["open"], r["high"], r["low"], r["close"] = o, h, l, c
            r["volume"], r["total_turnover"] = 100.0, 1000.0
            r["prev_close"] = pc
            r["limit_up"] = lu
            r["limit_down"] = (math.nan if math.isnan(lu) else round(lu * 0.9, 2))
            rows.append(r)
    arr = np.zeros(len(rows), dtype=BAR_DTYPE_MINUTE)
    for i, r in enumerate(rows):
        arr[i] = r
    return arr


def test_aggregate_normal_path_keeps_store_bands():
    bars = _bars([("2026-11-04", [("0935", 46.0, 47.5, 45.8, 47.3, 45.65, 50.2)]),
                  ("2026-11-05", [("0935", 47.4, 48.0, 47.0, 47.8, 47.3, 52.0)])])
    row, note = aggregate_day_from_minutes(bars, "20261105", "002192")
    assert note == ""
    assert (row["open"], row["high"], row["low"], row["close"]) == (47.4, 48.0, 47.0, 47.8)
    assert row["prev_close"] == 47.3 and row["limit_up"] == 52.0


def test_aggregate_falls_back_to_previous_trading_day_close():
    """prev_close/带 NaN ⇒ 用"上一交易日末根分钟收盘"回退，带按同一把尺子现算。"""
    bars = _bars([("2026-11-04", [("0935", 46.0, 47.5, 45.8, 47.3, math.nan, math.nan),
                                  ("1500", 47.0, 47.4, 46.9, 47.30, math.nan, math.nan)]),
                  ("2026-11-05", [("0935", 47.4, 48.0, 47.0, 47.8, math.nan, math.nan)])])
    row, note = aggregate_day_from_minutes(bars, "20261105", "002192")
    assert row is not None and "回退" in note
    assert row["prev_close"] == 47.30, "回退值必须是上一交易日的**末根** close"
    assert (row["limit_up"], row["limit_down"]) == limit_prices(47.30, "002192")
    assert np.isfinite(row["limit_up"]) and np.isfinite(row["limit_down"])


def test_aggregate_refuses_when_nothing_to_fall_back_to():
    """只有当天且当天带缺失 ⇒ 如实失败（返回 None + 原因），不拿 NaN 交出去。"""
    bars = _bars([("2026-11-05", [("0935", 47.4, 48.0, 47.0, 47.8, math.nan, math.nan)])])
    row, note = aggregate_day_from_minutes(bars, "20261105", "002192")
    assert row is None and "无上一交易日可退" in note


def test_aggregate_missing_day_is_not_a_zero_bar():
    bars = _bars([("2026-11-05", [("0935", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)])])
    row, note = aggregate_day_from_minutes(bars, "20261201", "002192")
    assert row is None and "当日无分钟 bar" in note


# ------------------------------------------------------------------ 前缀覆盖（Hermes 点名 6/0/3/688/8）
@pytest.mark.parametrize("code,obi", [
    ("600519", "600519.XSHG"),      # 沪主板
    ("601398", "601398.XSHG"),
    ("000651", "000651.XSHE"),      # 深主板
    ("002192", "002192.XSHE"),
    ("300750", "300750.XSHE"),      # 创业板
    ("688981", "688981.XSHG"),      # 科创板（68 必须归沪、20% 档）
    ("830799", "830799.BJ"),        # 北交所老段（首字符 8 不是深市）
    ("920156", "920156.BJ"),        # 北交所新段（首字符 9 不是沪市）
])
def test_order_book_id_prefix_coverage(code, obi):
    assert to_order_book_id(code) == obi
    assert markets.order_book_id(code) == obi      # 同一个口径，不是两份表


def test_bj_symbol_survives_the_init_chain_loudly():
    """北交所标的在期初链路上的完整行为：能序列化→框架查不到→**如实剔除**，不静默。

    开源 bundle 实测 28,063 只里 `.BJ` 有 0 只，所以今天它就是交易不了；
    要紧的是"因为查不到而丢掉"必须留下名字，而不是变成一笔看不见的缺仓。
    """
    ob = to_order_book_id("920156")
    assert init_positions_arg({ob: 100}) == "920156.BJ:100"
    cfg = build_config(today="2026-11-05", data_db="/tmp/d.db", bundle="/tmp/b",
                       symbols=["600519"], fee=FEE,
                       init_positions={ob: 100})
    assert cfg["base"]["init_positions"] == "920156.BJ:100"
    out = reconcile_init_positions({ob: 100}, [(ob, 100.0)],
                                   known=lambda x: x != ob)
    assert out["kept"] == [] and out["dropped_silently"] == []
    assert out["unsupported"] == [{"symbol": ob, "volume": 100}]
