"""回测资金守恒：买出的钱必须回到现金，卖出的钱必须进现金。

真机暴露过两个账目 bug：
1) 卖出回款没进现金 → 权益曲线凭空缩水（-32% 的"亏损"其实是没入账的钱）
2) 缺当日日线的票按 0 计价 → 基准收益被算成 -65%（实际约 -9%）
这两条用"资金守恒"这个不变量锁死。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.engine.backtest import Backtester, FeeModel  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def _bars(sym, days, closes):
    return [Bar(symbol=sym, timestamp=datetime.fromisoformat(d), open=c,
                high=c * 1.01, low=c * 0.99, close=c, volume=1000)
            for d, c in zip(days, closes)]


def test_sell_proceeds_return_to_cash():
    """一买一卖后现金必须回到"卖出净额"，而不是凭空蒸发。

    M1 口径：信号 T 日收盘 → T+1 开盘成交。第 3 天开盘 +5%（10.5，不是一字板）
    买入；第 4 天开盘清仓（约 +4.8%）；此后价格高于买入阈值，不再触发。
    """
    days = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05",
            "2026-03-06"]
    bars = {"A": _bars("A", days, [10.0, 10.0, 10.5, 11.0, 11.0])}
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 10,
           "max_turnover_per_day": 0,
           "rules": [
               {"id": "buy", "type": "price_level", "symbol": "A",
                "params": {"op": "below", "value": 10.6},
                "action": {"side": "buy", "value": 20000}, "once": False},
               {"id": "sell", "type": "price_level", "symbol": "A",
                "params": {"op": "above", "value": 10.5},
                "action": {"side": "sell", "all": True}, "once": False}]}
    res = Backtester(cfg, fee=FeeModel()).run(bars, days, initial_cash=100000)
    buys = [t for t in res.trades if t["side"] == "buy"]
    sells = [t for t in res.trades if t["side"] == "sell"]
    assert buys and sells, res.trades
    # 卖出回款 = 成交额 - 佣金 - 印花税 - 过户费
    expect = (sells[0]["amount"] - sells[0]["commission"] - sells[0]["stamp_duty"]
              - sells[0]["transfer_fee"])
    assert sells[0]["proceeds"] == round(expect, 2)
    # 现金守恒：初始 - 买入总额(含费) + 卖出净额 == 期末现金
    spent = sum(t["amount"] + t["commission"] + t["transfer_fee"] for t in buys)
    got = sum(t["proceeds"] for t in sells)
    last = res.equity_curve[-1]
    assert last["market_value"] == 0.0        # 已清仓
    assert last["cash"] == round(100000 - spent + got, 2), last
    # 平价进出后只剩手续费损耗，绝不该出现 3 万块的"亏损"
    assert last["equity"] > 99000, last


def test_equity_equals_cash_plus_market_value():
    days = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]
    bars = {"A": _bars("A", days, [10.0, 10.0, 10.0, 11.0])}
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 10,
           "max_turnover_per_day": 0,
           "rules": [{"id": "buy", "type": "price_level", "symbol": "A",
                      "params": {"op": "below", "value": 11},
                      "action": {"side": "buy", "value": 30000}, "once": False}]}
    res = Backtester(cfg, fee=FeeModel()).run(bars, days, initial_cash=100000)
    for row in res.equity_curve:
        assert row["equity"] == round(row["cash"] + row["market_value"], 2), row
    # 末日本金 + 市值 = 100000 - 买入费 + 浮盈
    last = res.equity_curve[-1]
    assert last["market_value"] > 0
    assert last["equity"] > 100000


def test_intraday_sell_proceeds_return_to_cash():
    """日内规则卖出后现金同样必须增加（曾与日线路径犯过同样的错）。"""
    days = ["2026-03-02", "2026-03-03"]
    bars = {"A": _bars("A", days, [10.0, 10.0])}
    mins = {}
    for day in days:
        t = datetime.fromisoformat(f"{day}T09:31")
        closes = [10, 10, 10, 9.0, 8.0, 12.0, 11.0, 11.0]
        mins[("A", day)] = []
        for i, c in enumerate(closes):
            ts = (t + timedelta(minutes=i)).isoformat(timespec="minutes")
            mins[("A", day)].append((ts, c, c * 1.002, c * 0.998, c,
                                     1000.0, c * 1000.0))
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 10,
           "max_turnover_per_day": 0,
           "rules": [{"id": "vw", "type": "vwap_dev", "symbol": "A",
                      "params": {"pct": 1, "min_bars": 3},
                      "action": {"side": "buy", "value": 20000}, "once": False},
                     {"id": "pb", "type": "intraday_pullback", "symbol": "A",
                      "params": {"drop_pct": 5}, "once": False}]}
    res = Backtester(cfg, fee=FeeModel()).run(bars, days, initial_cash=100000,
                                              minutes_by_day=mins)
    sells = [t for t in res.trades if t["side"] == "sell"]
    assert sells, "日内卖出应至少触发一次（拉高后回落）"
    for t in sells:
        assert t["proceeds"] > 0
    # 资金守恒
    buys = [t for t in res.trades if t["side"] == "buy"]
    spent = sum(t["amount"] + t["commission"] + t["transfer_fee"] for t in buys)
    got = sum(t["proceeds"] for t in sells)
    last = res.equity_curve[-1]
    assert last["cash"] == round(100000 - spent + got, 2), last
