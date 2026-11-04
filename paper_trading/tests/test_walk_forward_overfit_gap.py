"""T36 回归：walk-forward 的过拟合量度必须独立于"事后最优"。

缺陷原貌（T14 映射时发现，§5.5 裁决 7）：`optimize.py` 在按样本外重排**之后**
才取 `oracle = oos_rows[0]` 与 `picked = oos_rows[0]` —— 同一行，
于是"样本内第 1 名在样本外表现如何"这个纪律 7 要的数从来没被算过，
`picked` 也全文未被使用。
"""
from datetime import datetime as dt

import pytest

from paper_trading.engine.backtest import FeeModel
from paper_trading.engine.optimize import walk_forward
from paper_trading.models import Bar


def _bars(closes, sym):
    return [Bar(symbol=sym, timestamp=dt.fromisoformat(d), open=c,
                high=c * 1.01, low=c * 0.99, close=c, volume=1000)
            for d, c in closes]


def _days(n):
    out, d = [], 1
    for i in range(n):
        out.append(f"2026-05-{d:02d}" if d <= 31 else
                   (f"2026-06-{d - 31:02d}" if d <= 60 else f"2026-07-{d - 60:02d}"))
        d += 1
    return out


def _trend(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _wiggle(mid, amp, n):
    """每 3 天翻一次的锯齿：让均线在金叉死叉间来回挨打（样本内好看≠样本外好看的关键）。"""
    return [mid + amp * (1 if (i // 3) % 2 == 0 else -1) for i in range(n)]


def _fixture_two_symbols():
    """A 前半段单边上涨、后半段震荡；B 前半段震荡、后半段单边上涨。

    ⇒ 样本内排名第一的参数组，几乎必然不是样本外最好的那一组
    （旧实现把"事后最优"取成重排后的第 0 行，两个数于是永远相等）。
    """
    days = _days(90)
    a = _trend(10, 18, 54) + _wiggle(18, 0.55, 36)
    b = _wiggle(30, 0.95, 54) + _trend(30, 42, 36)
    return days, {"600519": _bars(list(zip(days, a)), "600519"),
                  "000001": _bars(list(zip(days, b)), "000001")}


@pytest.fixture()
def result():
    days, bars = _fixture_two_symbols()
    out = walk_forward({"rules": []}, bars, days, "ma_cross", ["600519", "000001"],
                       value=20000, fee=FeeModel(), initial_cash=100000,
                       split=0.6, top=5)
    assert not out.get("error"), out
    return out


def test_in_best_and_oracle_are_different_quantities(result):
    rows = result["out_of_sample"]
    assert len(rows) >= 2, "样本要能产出至少两个候选才能检验该缺陷"
    top_in = result["in_sample_top"][0]                      # 样本内第 1 名（排名权威来源）
    key = (top_in["symbol"], tuple(sorted(top_in["params"].items())))
    in_best_row = next(r for r in rows
                       if (r["symbol"], tuple(sorted(r["params"].items()))) == key)
    oracle = max(rows, key=lambda r: r["oos_excess_pct"])
    assert result["in_best_oos_excess_pct"] == in_best_row["oos_excess_pct"]
    assert result["in_best_oos_symbol"] == in_best_row["symbol"]
    assert result["in_best_oos_params"] == in_best_row["params"]
    assert result["oracle_oos_excess_pct"] == oracle["oos_excess_pct"]
    assert result["oracle_oos_symbol"] == oracle["symbol"]


def test_overfit_gap_is_oracle_minus_in_best(result):
    want = round(float(result["oracle_oos_excess_pct"])
                 - float(result["in_best_oos_excess_pct"]), 2)
    assert result["overfit_gap_pct"] == want
    assert result["overfit_gap_pct"] >= 0            # 事后最优不可能比样本内第 1 名差


def test_fixture_really_has_divergence(result):
    """前提检查：本 fixture 必须能让"样本内第 1 名"与"事后最优"是不同候选。"""
    assert (result["in_best_oos_symbol"], tuple(sorted(result["in_best_oos_params"].items())))         != (result["oracle_oos_symbol"], tuple(sorted(result["oracle_oos_params"].items())))


def test_old_bug_shape_would_be_caught(result):
    """旧实现两数取自同一行 ⇒ 恒为 0；现在必须量出正的差距。"""
    assert result["overfit_gap_pct"] > 0
    assert result["oracle_oos_excess_pct"] > result["in_best_oos_excess_pct"]


def test_legacy_keys_still_present_for_panel(result):
    for k in ("split", "in_sample_top", "out_of_sample", "n_oos_positive",
              "n_tested", "sample_in_best_excess", "oracle_oos_excess", "note"):
        assert k in result
    assert result["oracle_oos_excess"] == result["oracle_oos_excess_pct"]
    assert "过拟合差距" in result["note"]


def test_single_candidate_gaps_to_zero():
    """只有一个候选时，样本内第 1 名就是事后最优，差距必须为 0 而不是 None/缺失。"""
    days = _days(90)
    closes = [10.0 if i < 25 else 10.0 + (i - 25) * 0.3 for i in range(90)]
    out = walk_forward({"rules": []}, {"600519": _bars(list(zip(days, closes)), "600519")},
                       days, "ma_cross", ["600519"], value=20000, fee=FeeModel(),
                       initial_cash=100000, split=0.6, top=5)
    assert not out.get("error"), out
    assert out["n_tested"] >= 1
    assert out["overfit_gap_pct"] == 0.0
    assert out["in_best_oos_excess_pct"] == out["oracle_oos_excess_pct"]
