"""T17 回归：被跳过的信号必须**分类可见**，不许只剩一个总数或一段样本（AGENTS 路线图纪律 4「不静默」）。

真机暴露（worker/report-t9.md F 表）：`action.value = 100 × 窗口首根收盘价` 时，
价格一旦上穿首价，`size_for_value()` 向下取整到整手 ⇒ 买 0 手 ⇒ 信号整类消失，
实测一个 8 个月窗口里「金额不足一手」14 笔 + 连带「无可卖持仓」15 笔；
而 `metrics()` 只报 `skipped` 总数，CLI/面板再截 10~20 条样本，汇总里看不见整类流失。
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.engine.backtest import Backtester, FeeModel, metrics  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def _series(sym, closes):
    days, out = [], []
    for i, c in enumerate(closes):
        assert i < 31, "样本控制在 31 天内（日历日直接编号）"
        day = f"2026-03-{i + 1:02d}"
        days.append(day)
        out.append(Bar(symbol=sym, timestamp=datetime.fromisoformat(day),
                       open=c, high=c * 1.01, low=c * 0.99, close=c, volume=10_000))
    return out, days


def _cfg(value):
    return {"min_ticket_cny": 0, "max_orders_per_day": 0, "max_turnover_per_day": 0,
            "rules": [{"id": "ma-A", "type": "ma_cross", "symbol": "A",
                       "params": {"short": 5, "long": 20},
                       "action": {"side": "buy", "value": value}, "once": True}]}


def _flat_then_jump():
    """前 21 根 10.0，第 22 根起 30.0 ⇒ 第 22 根金叉（MA5>MA20 且前一日 ≤）。"""
    return [10.0] * 21 + [30.0] * 4


def test_amount_below_one_lot_is_reported_by_class():
    closes = _flat_then_jump()
    bars, days = _series("A", closes)
    # value = 100 股 × 窗口首价 10.0 = 1000 元；成交价 30 ⇒ 买 0 手（金额不足一手）
    res = Backtester(_cfg(1000.0), fee=FeeModel(), slippage_pct=0.0,
                     slippage_fixed=0.0).run({"A": bars}, days, initial_cash=100_000.0)

    assert res.trades == [], f"本例不该有成交: {res.trades}"
    whys = [str(k.get("why") or "") for k in res.skipped]
    assert any("金额不足一手" in w for w in whys), whys

    m = metrics(res, len(days))
    assert m.get("skip_counts"), m
    assert m["skip_counts"].get("金额不足一手", 0) >= 1, m["skip_counts"]
    # 分类计数必须完整覆盖每一条跳过（不静默 = 没有暗账）
    assert sum(m["skip_counts"].values()) == m["skipped"] == len(res.skipped), m
    assert m["order_attempts"] == len(res.trades) + len(res.skipped), m
    assert m["signal_drop_pct"] > 0.0, m


def test_every_skip_has_a_reason():
    closes = _flat_then_jump()
    bars, days = _series("A", closes)
    res = Backtester(_cfg(1000.0), fee=FeeModel(), slippage_pct=0.0,
                     slippage_fixed=0.0).run({"A": bars}, days, initial_cash=100_000.0)
    for k in res.skipped:
        assert str(k.get("why") or "").strip(), f"跳过项缺归因: {k}"
    m = metrics(res, len(days))
    assert "未归类" not in (m.get("skip_counts") or {}), m["skip_counts"]


def test_clean_run_reports_zero_drop():
    """金叉且金额够用 ⇒ 有成交、无流失：skip_counts 为空、流失率 0。"""
    closes = _flat_then_jump()
    bars, days = _series("A", closes)
    res = Backtester(_cfg(3000.0), fee=FeeModel(), slippage_pct=0.0,
                     slippage_fixed=0.0).run({"A": bars}, days, initial_cash=100_000.0)
    m = metrics(res, len(days))
    assert res.trades, "本例应能建仓"
    assert m["skip_counts"] == {}, m["skip_counts"]
    assert m["skipped"] == 0 and m["signal_drop_pct"] == 0.0, m
