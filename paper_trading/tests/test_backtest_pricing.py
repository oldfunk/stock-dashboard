"""回测市值口径：某只票当天缺数据时，权益曲线不能凭空塌一块。

真机暴露：3 只票里有 2 只的最新日线比另 1 只早一天，最后一天它们没有收盘价，
旧实现把它们的持仓当成 0 计价 → 基准收益被算成 -65%（实际约 -9%）。
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.engine.backtest import Backtester, FeeModel, buy_and_hold  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def _bars(sym, days, close):
    return [Bar(symbol=sym, timestamp=datetime.fromisoformat(d), open=c,
                high=c * 1.01, low=c * 0.99, close=c, volume=1000)
            for d, c in zip(days, close)]


def test_benchmark_carries_forward_missing_day():
    days = ["2026-03-02", "2026-03-03", "2026-03-04"]
    # A 有全部 3 天；B 只到第 2 天（模拟"最新日线比别的票早一天"）
    bars = {
        "A": _bars("A", days, [10.0, 10.0, 10.0]),
        "B": _bars("B", days[:2], [10.0, 10.0]),
    }
    bench = buy_and_hold(bars, days, initial_cash=100000, fee=FeeModel())
    # 两只票都没跌：基准收益应为 0（减去买入费用），绝不是 -50%
    assert bench["total_return_pct"] > -2, bench
    assert abs(bench["total_return_pct"]) < 2


def test_equity_curve_not_dive_on_missing_day():
    days = ["2026-03-02", "2026-03-03", "2026-03-04"]
    bars = {"A": _bars("A", days, [10.0, 10.0, 10.0])}
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 10,
           "rules": [{"id": "buy", "type": "price_level", "symbol": "A",
                      "params": {"op": "below", "value": 11},
                      "action": {"side": "buy", "value": 20000}, "once": False}]}
    res = Backtester(cfg, fee=FeeModel()).run(bars, days, initial_cash=100000)
    eq = [c["equity"] for c in res.equity_curve]
    assert len(eq) == 3
    # 末日缺数据时，权益应沿用最近估值，不出现腰斩
    assert min(eq) > 50000, eq
