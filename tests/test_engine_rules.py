"""规则引擎单测：判定逻辑 / 费用口径一致 / 回测纪律（禁未来函数、无网）。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.engine.backtest import (Backtester, FeeModel,  # noqa: E402
                                            buy_and_hold, metrics)
from paper_trading.engine.config import validate  # noqa: E402
from paper_trading.engine.rules import (PositionView, RuleContext,  # noqa: E402
                                         evaluate, rsi, size_for_value)
from paper_trading.engine.state import EngineState  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def _ctx(**kw):
    base = dict(now="2026-10-19T10:00:00", day="2026-10-19", symbol="600519",
                price=10.0, prev_close=9.9, day_high=10.2, day_low=9.8,
                change_pct=1.0, closes=[10.0] * 25, position=None,
                trading_days_held=0)
    base.update(kw)
    return RuleContext(**base)


# ---------- 规则判定 ----------

def test_ma_cross_fires_only_on_cross():
    up = [10, 10, 10, 10, 10, 10, 10, 10, 10, 10] + [9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 12]
    r = {"id": "m1", "type": "ma_cross", "symbol": "600519",
         "params": {"short": 3, "long": 10}, "action": {"side": "buy", "value": 8000}}
    it = evaluate(r, _ctx(closes=up))
    assert it and it.side == "buy" and it.rule_id == "m1"
    down = up[:-1] + [5]
    # 方向型规则由信号决定方向：死叉即卖出（action.side 不覆盖）
    it2 = evaluate(r, _ctx(closes=down))
    assert it2 and it2.side == "sell"
    # 非方向型规则仍由 action.side 决定
    rl = {"id": "p9", "type": "price_level", "symbol": "600519",
          "params": {"op": "below", "value": 10.5},
          "action": {"side": "sell"}}
    it3 = evaluate(rl, _ctx(price=10.0))
    assert it3 and it3.side == "sell"


def test_rsi_zone_and_rsi_math():
    assert rsi([10] * 20) == 50.0          # 无波动按中性 50
    assert rsi([1, 2]) is None
    falling = list(range(40, 0, -1))
    assert rsi(falling, 14) < 30
    rising = list(range(1, 41))
    assert rsi(rising, 14) > 70
    # 构造一次真实穿越：先超买后超卖
    zig = list(range(1, 21)) + list(range(20, 0, -1))
    n, lower, upper = 14, 70, 30
    prev, cur = rsi(zig[:-1], n), rsi(zig, n)
    assert prev is not None and cur is not None
    r = {"id": "r1", "type": "rsi_zone", "symbol": "600519",
         "params": {"n": n, "lower": lower, "upper": upper},
         "action": {"side": "buy"}}
    it = evaluate(r, _ctx(closes=zig))
    if prev > lower >= cur:
        assert it and it.side == "buy"
    elif prev < upper <= cur:
        assert it and it.side == "sell"
    else:
        assert it is None


def test_price_level_and_pct_change():
    r = {"id": "p1", "type": "price_level", "symbol": "600519",
         "params": {"op": "below", "value": 10.5},
         "action": {"side": "buy", "value": 8000}}
    assert evaluate(r, _ctx(price=10.0)) is not None
    assert evaluate(r, _ctx(price=11.0)) is None
    rp = {"id": "p2", "type": "pct_change", "symbol": "600519",
          "params": {"pct": 3}, "action": {"side": "sell", "all": True}}
    assert evaluate(rp, _ctx(change_pct=-4.0)).side == "buy"    # 跌→买
    assert evaluate(rp, _ctx(change_pct=3.5)).side == "sell"   # 涨→卖


def test_trailing_and_time_stop():
    pos = PositionView(symbol="600519", total_volume=200, available_volume=200,
                       avg_cost=10.0, peak_price=12.0)
    r = {"id": "t1", "type": "trailing_stop", "symbol": "600519",
         "params": {"pct": 5}, "action": {"side": "sell", "all": True}}
    it = evaluate(r, _ctx(position=pos, price=11.0))
    assert it and it.side == "sell" and it.all_out
    assert evaluate(r, _ctx(position=pos, price=11.9)) is None
    rt_ = {"id": "t2", "type": "time_stop", "symbol": "600519",
           "params": {"days": 3}, "action": {"side": "sell", "all": True}}
    assert evaluate(rt_, _ctx(position=pos, trading_days_held=4)) is not None
    assert evaluate(rt_, _ctx(position=pos, trading_days_held=1)) is None
    # 无持仓不触发
    assert evaluate(rt_, _ctx(position=None, trading_days_held=9)) is None


def test_evaluate_fail_closed():
    assert evaluate({"id": "x", "type": "unknown"}, _ctx()) is None
    assert evaluate({"id": "x", "type": "price_level"}, _ctx()) is None   # 无标的
    assert evaluate({"id": "x", "type": "price_level", "symbol": "A",
                     "params": {"value": 1}}, _ctx()) is None            # 价格 0


def test_size_for_value_lots():
    assert size_for_value(23.07, 5000) == 200      # 5000/23.07≈216 → 200
    assert size_for_value(267.85, 20000) == 0 or size_for_value(267.85, 20000) == 0
    assert size_for_value(10.0, 0) == 0 and size_for_value(0, 1000) == 0


# ---------- 费用口径（与 PaperBroker 一致） ----------

def test_fee_model_matches_broker(tmp_path):
    import tempfile

    import paper_trading.cli as hb

    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    fee = FeeModel()
    for amount in (1000.0, 5000.0, 20000.0, 123456.0):
        assert fee.buy_cost(amount) == b.broker._calc_buy_cost(amount)
        assert fee.sell_cost(amount) == b.broker._calc_sell_cost(amount)


# ---------- 配置校验 ----------

def test_config_validation():
    good = {"interval_sec": 60, "max_orders_per_day": 6, "rules": [
        {"id": "a", "type": "price_level", "symbol": "600519",
         "params": {"op": "below", "value": 10}, "action": {"side": "buy"}}]}
    assert validate(good)[0]
    bad = {"interval_sec": 60, "rules": [
        {"id": "a", "type": "price_level", "symbol": "600519", "action": {"side": "hold"}}]}
    assert not validate(bad)[0]      # 动作非法
    dup = {"interval_sec": 60, "rules": [
        {"id": "a", "type": "price_level", "symbol": "1", "action": {"side": "buy"}},
        {"id": "a", "type": "price_level", "symbol": "2", "action": {"side": "buy"}}]}
    assert not validate(dup)[0]      # id 重复
    assert not validate({"interval_sec": 1})[0]


# ---------- 回测纪律 ----------

def _bars(days, closes, symbol="600519"):
    """按给定交易日列表造 bars（日期必须与回测 days 一致）。"""
    out = []
    for d, c in zip(days, closes):
        ts = datetime.fromisoformat(d + "T00:00:00")
        out.append(Bar(symbol=symbol, timestamp=ts, open=c, high=c * 1.01,
                       low=c * 0.99, close=c, volume=1000))
    return out


def _days(month: int, n: int) -> list[str]:
    return [f"2026-{month:02d}-{d:02d}" for d in range(1, n + 1)]


def test_backtest_no_lookahead_and_t_plus_1():
    """T 日收盘信号最早 T+1 成交（M1：T+1 **开盘价** ± 固定滑点 0.01）。"""
    days = _days(1, 8)
    closes = [10, 10, 10, 9, 9, 20, 20, 20]      # 第6天大涨
    bars = {"600519": _bars(days, closes)}
    cfg = {"min_ticket_cny": 5000, "max_orders_per_day": 99,
           "max_turnover_per_day": 0, "rules": [
               {"id": "px", "type": "price_level", "symbol": "600519",
                "params": {"op": "above", "value": 15},
                "action": {"side": "buy", "value": 10000}}]}
    res = Backtester(cfg).run(bars, days, initial_cash=100000)
    buys = [t for t in res.trades if t["side"] == "buy"]
    # M1：once=按交易日（不是整段回测一次）。第 6 天收盘出信号 → 第 7 天开盘
    # 20.01 买入；第 7 天收盘仍满足 → 第 8 天开盘再买一次（与实盘每日判定一致）。
    assert len(buys) == 2
    assert [t["day"] for t in buys] == [days[6], days[7]]
    assert all(t["price"] == 20.01 for t in buys)


def test_backtest_min_ticket_and_fees():
    days = _days(2, 8)
    closes = [10, 10, 10, 10, 10, 10, 10, 10]
    bars = {"600519": _bars(days, closes)}
    cfg = {"min_ticket_cny": 5000, "max_orders_per_day": 99,
           "max_turnover_per_day": 0, "rules": [
               {"id": "px", "type": "price_level", "symbol": "600519",
                "params": {"op": "below", "value": 10.5},
                "action": {"side": "buy", "value": 1000}}]}
    res = Backtester(cfg).run(bars, days, initial_cash=100000)
    assert not [t for t in res.trades if t["side"] == "buy"]     # 低于最小金额
    assert any("最小下单金额" in str(s.get("why", "")) for s in res.skipped)


def test_backtest_once_rule_and_metrics():
    days = _days(3, 8)
    closes = [10, 10, 10, 9, 9, 20, 21, 22]
    bars = {"600519": _bars(days, closes)}
    cfg = {"min_ticket_cny": 1000, "max_orders_per_day": 99,
           "max_turnover_per_day": 0, "rules": [
               {"id": "px", "type": "price_level", "symbol": "600519",
                "params": {"op": "above", "value": 15},
                "action": {"side": "buy", "value": 10000},
                "once": True},
               {"id": "sell", "type": "price_level", "symbol": "600519",
                "params": {"op": "above", "value": 20.5},
                "action": {"side": "sell", "all": True}}]}
    res = Backtester(cfg).run(bars, days, initial_cash=100000)
    m = metrics(res, len(days))
    assert m["trades"] >= 2 and m["final_equity"] != 100000
    assert 0 <= m["win_rate_pct"] <= 100
    assert m["total_fees"] > 0
    assert len(res.equity_curve) == len(days)


# ---------- 运行态（预算/冷却/触发落库） ----------

def test_engine_local_overrides_main(tmp_path):
    """engine.local.yaml（gitignored）逐层覆盖 config.yaml，且结构同为 engine: 段。"""
    from paper_trading.engine.config import load_engine_config, save_engine_config

    main = tmp_path / "config.yaml"
    main.write_text("engine:\n  enabled: false\n  min_ticket_cny: 5000\n  rules: []\n",
                    encoding="utf-8")
    ok, msg = save_engine_config(tmp_path, {
        "enabled": True, "interval_sec": 30, "min_ticket_cny": 8000,
        "max_orders_per_day": 3, "max_turnover_per_day": 1000.0,
        "per_symbol_cooldown_sec": 60,
        "rules": [{"id": "r1", "type": "price_level", "symbol": "600519",
                   "params": {"op": "below", "value": 10},
                   "action": {"side": "buy", "value": 5000}}]})
    assert ok, msg
    cfg = load_engine_config(str(main), root=tmp_path)
    assert cfg["enabled"] is True and cfg["interval_sec"] == 30
    assert cfg["min_ticket_cny"] == 8000 and len(cfg["rules"]) == 1
    # 非法规则不落盘
    assert not save_engine_config(tmp_path, {"interval_sec": 30, "rules": [
        {"id": "x", "type": "nope", "symbol": "1", "action": {"side": "buy"}}]})[0]
    assert load_engine_config(str(main), root=tmp_path)["rules"][0]["id"] == "r1"


def test_buy_and_hold_benchmark():
    days = _days(4, 5)
    closes = [10, 11, 12, 13, 14]
    bars = {"600519": _bars(days, closes)}
    b = buy_and_hold(bars, days, 100000.0, FeeModel())
    assert b["final_equity"] > 100000 and b["total_return_pct"] > 0
    assert b["total_fees"] >= 5.0        # 最低佣金已计入
    flat = buy_and_hold({"600519": _bars(days, [10] * 5)}, days, 100000.0, FeeModel())
    assert flat["total_return_pct"] < 0    # 只有费用拖累


def test_engine_state_persists(tmp_path):
    db = str(tmp_path / "acct.db")
    st = EngineState(db)
    assert st.budget("2026-10-19")["orders"] == 0
    st.bump_budget("2026-10-19", 1, 1234.5)
    st2 = EngineState(db)                      # 模拟重启
    assert st2.budget("2026-10-19")["orders"] == 1
    assert st2.budget("2026-10-19")["turnover"] == 1234.5
    st2.mark_fired("r1", "2026-10-19")
    assert EngineState(db).fired_rules("2026-10-19") == {"r1"}
    st2.mark_order("600519", "2026-10-19T10:00:00")
    assert EngineState(db).last_order_ts("600519") == "2026-10-19T10:00:00"
    st2.log_trigger("2026-10-19T10:00:00", "2026-10-19", "r1", "600519",
                    "buy", "filled", {"price": 10})
    trg = EngineState(db).triggers_today("2026-10-19")
    assert trg and trg[0]["status"] == "filled" and trg[0]["detail"]["price"] == 10