"""M1 正确性与回测-实盘口径统一回归测试（对应 docs/ROADMAP.md M1）。

覆盖五组审计修复：
- M1-A 持仓元数据（entry_day/peak）落库、旧库迁移、除权除息人工调整
- M1-B 回测 T+1 开盘成交、一字板/停牌不成交、once=按日、风控接入
- M1-C 风控峰值/日亏基线落库、按标的真实价格计价、引擎排除未收盘日线、
       条件单与规则引擎共用日内预算
- M1-D 连续竞价时段、报价新鲜度（节假日/过期报价不进场）
- M1-E 规则级开关、universe=auto/list、科创板 200 股、除权回溯检测
"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.broker.paper_broker import PaperBroker  # noqa: E402
from paper_trading.data import realtime as rt  # noqa: E402
from paper_trading.engine import runner as runner_mod  # noqa: E402
from paper_trading.engine.backtest import Backtester  # noqa: E402
from paper_trading.engine.conditional import ConditionalOrders  # noqa: E402
from paper_trading.engine.config import validate  # noqa: E402
from paper_trading.engine.runner import (ConditionRunner, EngineRunner,  # noqa: E402
                                         account_guard, bars_excluding_today,
                                         price_map)
from paper_trading.engine.state import EngineState  # noqa: E402
from paper_trading.models import (Bar, Order, OrderType, Position,  # noqa: E402
                                  Signal, SignalType, TradingConfig)
from paper_trading.risk.risk_manager import RiskManager  # noqa: E402


# ---------- 测试工具 ----------

def _bar(sym: str, day: str, close: float, open_: float | None = None,
         high: float | None = None, low: float | None = None) -> Bar:
    o = close if open_ is None else open_
    return Bar(symbol=sym, timestamp=datetime.fromisoformat(day), open=o,
               high=max(o, close) * 1.01 if high is None else high,
               low=min(o, close) * 0.99 if low is None else low,
               close=close, volume=1000)


def _bars(sym: str, days: list[str], closes: list[float],
          opens: list[float] | None = None) -> list[Bar]:
    return [_bar(sym, d, c, open_=(opens[i] if opens else None))
            for i, (d, c) in enumerate(zip(days, closes))]


def _days(n: int) -> list[str]:
    return [f"2026-01-{i:02d}" for i in range(1, n + 1)]


def _order(sym: str, side: str, volume: int, price: float) -> Order:
    return Order(symbol=sym, direction=1 if side == "buy" else -1,
                 volume=volume, order_type=OrderType.LIMIT, limit_price=price)


def _bridge(tmp_path):
    from paper_trading.cli import TradingBridge
    return TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "account.db"),
                         config=TradingConfig(initial_cash=100000),
                         config_path=str(tmp_path / "missing_config.yaml"))


# ---------- M1-A 持仓元数据 / 除权除息 ----------

def test_position_metadata_written_and_persisted(tmp_path):
    db = str(tmp_path / "acc.db")
    b = PaperBroker(db, TradingConfig(initial_cash=100000))
    o1 = b.submit_order(_order("600519", "buy", 100, 10.0))
    assert o1.status.value == "filled"
    p1 = b.get_position("600519")
    assert p1.entry_day == datetime.now().date().isoformat()
    assert p1.peak_price == pytest.approx(o1.filled_price)
    assert p1.available_volume == 0          # T+1

    o2 = b.submit_order(_order("600519", "buy", 100, 12.0))
    p2 = b.get_position("600519")
    assert p2.entry_day == p1.entry_day      # 加仓不重置建仓日
    assert p2.peak_price == pytest.approx(o2.filled_price)

    b.update_peak_price("600519", 20.0)
    b2 = PaperBroker(db, TradingConfig(initial_cash=100000))
    assert b2.get_position("600519").peak_price == pytest.approx(20.0)

    # 部分卖出保留建仓日/峰值；清仓删除持仓
    b2.unfreeze_t1(datetime.now() + timedelta(days=10))
    o3 = b2.submit_order(_order("600519", "sell", 100, 20.0))
    assert o3.status.value == "filled"
    p3 = b2.get_position("600519")
    assert p3.total_volume == 100
    assert p3.entry_day == p1.entry_day
    assert p3.peak_price == pytest.approx(20.0)
    assert b2.submit_order(_order("600519", "sell", 100, 20.0)).status.value == \
        "filled"
    assert b2.get_position("600519") is None


def test_legacy_positions_migration_backfills_entry_day(tmp_path):
    f = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(f)
    conn.execute("""CREATE TABLE positions (symbol TEXT PRIMARY KEY,
        total_volume INTEGER NOT NULL, available_volume INTEGER NOT NULL,
        avg_cost REAL NOT NULL, last_update TEXT NOT NULL)""")
    conn.execute("INSERT INTO positions VALUES ('600519', 1000, 1000, 10.0,"
                 " '2026-09-01T10:00:00')")
    conn.execute("""CREATE TABLE fills (order_id INTEGER NOT NULL,
        symbol TEXT NOT NULL, direction INTEGER NOT NULL, volume INTEGER NOT NULL,
        price REAL NOT NULL, commission REAL NOT NULL, stamp_duty REAL NOT NULL,
        transfer_fee REAL NOT NULL, timestamp TEXT NOT NULL)""")
    conn.execute("INSERT INTO fills VALUES (1, '600519', 1, 1000, 9.5, 5.0, 0.0,"
                 " 0.1, '2026-09-01T09:35:00')")
    conn.commit()
    conn.close()

    b = PaperBroker(f, TradingConfig(initial_cash=100000))
    p = b.get_position("600519")
    assert p.entry_day == "2026-09-01"        # 从最早买入成交回填
    assert p.peak_price == pytest.approx(10.0)  # 无历史高点时按成本兜底


def test_apply_corporate_action_dividend_and_bonus(tmp_path):
    b = PaperBroker(str(tmp_path / "acc.db"), TradingConfig(initial_cash=100000))
    b.submit_order(_order("000001", "buy", 1000, 10.0))
    p = b.get_position("000001")
    cash0 = b.get_cash()
    out = b.apply_corporate_action("000001", "2026-06-20",
                                   cash_per_share=0.5, share_ratio=0.3)
    assert out["ok"]
    assert out["cash_dividend"] == pytest.approx(500.0)
    assert out["bonus_shares"] == 300
    p2 = b.get_position("000001")
    assert p2.total_volume == 1300
    assert p2.available_volume == p.available_volume   # 送股不增加可卖量
    assert b.get_cash() == pytest.approx(cash0 + 500.0)
    assert p2.avg_cost == pytest.approx((p.avg_cost * 1000 - 500.0) / 1300,
                                        rel=1e-6)
    exp_peak = (max(p.peak_price, p.avg_cost) - 0.5) / 1.3
    assert p2.peak_price == pytest.approx(exp_peak, rel=1e-4)
    assert any(r["action"] == "corporate:apply" for r in b.get_op_log(10))
    # 无持仓不静默改账
    assert b.apply_corporate_action("600519", "2026-06-20", 0.1, 0.0)["ok"] is False


# ---------- M1-B 回测口径 ----------

def test_backtest_limit_up_open_blocks_buy():
    days = _days(6)
    # 第 3 天开盘 11（前收 10，恰好 +10% 一字涨停），第 2 天收盘的信号不成交
    bars = {"A": _bars("A", days, [10, 10, 10, 10, 10, 10],
                       opens=[10, 10, 11, 10, 10, 10])}
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 99,
           "max_turnover_per_day": 0,
           "rules": [{"id": "b", "type": "price_level", "symbol": "A",
                      "params": {"op": "above", "value": 9.5},
                      "action": {"side": "buy", "value": 5000},
                      "once": False}]}
    res = Backtester(cfg).run(bars, days, initial_cash=100000)
    assert any("开盘涨停" in s["why"] for s in res.skipped)
    assert all(t["day"] != days[2] for t in res.trades)


def test_backtest_position_cap_rejects_and_risk_off_disables():
    days = _days(7)
    bars = {"A": _bars("A", days, [10] * 7)}
    base = {"min_ticket_cny": 0, "max_orders_per_day": 99,
            "max_turnover_per_day": 0,
            "rules": [{"id": "b", "type": "price_level", "symbol": "A",
                       "params": {"op": "below", "value": 11},
                       "action": {"side": "buy", "value": 30000},
                       "once": False}]}
    on = Backtester(dict(base)).run(bars, days, initial_cash=100000)
    off = Backtester(dict(base), risk={}).run(bars, days, initial_cash=100000)
    on_buys = [t for t in on.trades if t["side"] == "buy"]
    off_buys = [t for t in off.trades if t["side"] == "buy"]
    assert any("风控拒绝" in s["why"] for s in on.skipped)
    assert len(off_buys) > len(on_buys)


def test_backtest_vs_simulated_live_tick_parity(tmp_path, monkeypatch):
    """同一规则、同一日线、同一风控参数：回测与逐日模拟实盘 tick 成交日/价一致。"""
    days = _days(8)
    closes = [10, 10, 10, 9, 9, 20, 20, 20]
    # 第 6 天盘中涨到 20（开盘 9），第 7/8 天开在 20——避免隔夜跳空带来的口径差
    opens = [10, 10, 10, 9, 9, 9, 20, 20]
    bars = _bars("600519", days, closes, opens=opens)
    cfg = {"enabled": True, "interval_sec": 60, "universe": "list",
           "max_orders_per_day": 6, "max_turnover_per_day": 0,
           "per_symbol_cooldown_sec": 0, "min_ticket_cny": 5000,
           "rules": [{"id": "px", "type": "price_level", "symbol": "600519",
                      "params": {"op": "above", "value": 15},
                      "action": {"side": "buy", "value": 10000}}]}

    bt = Backtester(cfg).run({"600519": bars}, days, initial_cash=100000)
    bt_buys = [(t["day"], t["price"]) for t in bt.trades if t["side"] == "buy"]

    bridge = _bridge(tmp_path)
    bridge.data_db.upsert_bars(bars)
    runner = EngineRunner(bridge, cfg=cfg,
                          state=EngineState(bridge.account_db_path))
    quotes: dict = {}
    monkeypatch.setattr(rt, "is_trading_session", lambda now=None: True)
    monkeypatch.setattr(rt, "get_quotes", lambda codes, force=False: quotes)

    def _freeze(day: str):
        class _FixedDT(datetime):
            @classmethod
            def now(cls, tz=None):              # noqa: ARG003
                return cls.fromisoformat(day + "T09:35:00")
        return _FixedDT

    live_buys = []
    for i, day in enumerate(days):
        monkeypatch.setattr(runner_mod, "datetime", _freeze(day))
        quotes.clear()
        quotes["600519"] = {
            "price": opens[i],
            "prev_close": closes[i - 1] if i else closes[0],
            "high": max(opens[i], closes[i]),
            "low": min(opens[i], closes[i]),
            "change_pct": 0.0,
        }
        out = runner.tick()
        live_buys.extend((day, f["price"]) for f in (out.get("fired") or [])
                         if f.get("side") == "buy")

    assert bt_buys == live_buys == [(days[6], 20.01), (days[7], 20.01)]


def test_backtest_suspension_no_fill_and_resumes():
    days = _days(6)
    # A 第 3 天停牌（没有 K 线）
    bars = {"A": _bars("A", [days[0], days[1], days[3], days[4], days[5]],
                       [10] * 5)}
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 99,
           "max_turnover_per_day": 0,
           "rules": [{"id": "b", "type": "price_level", "symbol": "A",
                      "params": {"op": "above", "value": 9.5},
                      "action": {"side": "buy", "value": 5000},
                      "once": False}]}
    res = Backtester(cfg).run(bars, days, initial_cash=100000)
    assert any("停牌" in s["why"] for s in res.skipped)
    assert all(t["day"] != days[2] for t in res.trades)


def test_backtest_rule_enabled_false_is_skipped():
    days = _days(5)
    bars = {"A": _bars("A", days, [10] * 5)}
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 9,
           "max_turnover_per_day": 0,
           "rules": [{"id": "off", "type": "price_level", "symbol": "A",
                      "params": {"op": "above", "value": 5},
                      "action": {"side": "buy", "value": 5000},
                      "enabled": False}]}
    res = Backtester(cfg).run(bars, days, initial_cash=100000)
    assert res.trades == []


# ---------- M1-C 风控落库 / 计价 / 共享预算 ----------

def test_risk_state_persists_peak_and_daily_baseline(tmp_path):
    f = str(tmp_path / "acc.db")
    rm = RiskManager(db_path=f, max_drawdown_pct=0.2, daily_loss_halt_pct=0.05)
    rm.update_peak(120000.0)
    halted, loss = rm.check_daily_loss(120000.0, "2026-10-20")
    assert not halted and loss == pytest.approx(0.0)

    # 新进程（新实例）仍读得到峰值与日亏基线
    rm2 = RiskManager(db_path=f, max_drawdown_pct=0.2, daily_loss_halt_pct=0.05)
    halted, dd = rm2.check_drawdown(90000.0)
    assert halted and dd == pytest.approx(0.25)
    halted, loss = rm2.check_daily_loss(110000.0, "2026-10-20")
    assert halted and loss == pytest.approx(10000.0 / 120000.0)
    # 换一天重新锚定基线（日亏是"当日"口径）
    halted, loss = rm2.check_daily_loss(110000.0, "2026-10-21")
    assert not halted and loss == pytest.approx(0.0)


def test_sell_not_blocked_by_single_order_value_cap():
    rm = RiskManager(max_single_order_value=1000.0)
    pos = {"A": Position(symbol="A", total_volume=1000, available_volume=1000,
                         avg_cost=10.0, last_update=datetime.now())}
    sell = Signal(symbol="A", direction=SignalType.SELL, volume=1000, price=10.0)
    ok, why = rm.check_signal(sell, 10.0, cash=0.0, positions=pos,
                              total_value=10000.0, prices={"A": 10.0})
    assert ok, why                            # 卖出是出清，不被单笔金额卡住
    buy = Signal(symbol="B", direction=SignalType.BUY, volume=1000, price=10.0)
    ok, why = rm.check_signal(buy, 10.0, cash=100000.0, positions=pos,
                              total_value=100000.0, prices={"A": 10.0})
    assert not ok and "exceeds max" in why


def test_account_guard_prices_each_symbol_with_own_price(tmp_path):
    bridge = _bridge(tmp_path)
    b = bridge.broker
    b.submit_order(_order("000001", "buy", 100, 10.0))
    b.submit_order(_order("000002", "buy", 200, 20.0))
    bridge.data_db.upsert_bars([_bar("000002", "2026-01-05", 20.0)])

    # 只有 000001 有实时报价；000002 必须用自己最近的收盘价，不能拿别家价格填
    prices = price_map(bridge, {"000001": {"price": 11.0}},
                       ["000001", "000002"])
    assert prices["000001"] == pytest.approx(11.0)
    assert prices["000002"] == pytest.approx(20.0)
    guard = account_guard(bridge, prices, "2026-01-05")
    assert not guard["halted"]
    cash = b.get_cash()
    assert guard["nav"].total_value == pytest.approx(
        cash + 100 * 11.0 + 200 * 20.0, rel=1e-9)

    # 同一"日"基线已锚定，000002 跌到 14 → 当日亏损超过 1% 触发熔断
    bridge.risk.daily_loss_halt_pct = 0.01
    bridge.data_db.upsert_bars([_bar("000002", "2026-01-06", 14.0)])
    prices2 = price_map(bridge, {"000001": {"price": 11.0}},
                        ["000001", "000002"])
    guard2 = account_guard(bridge, prices2, "2026-01-05")
    assert guard2["halted"] and "当日亏损" in guard2["why"]


def test_engine_excludes_unfinished_today_bar():
    d0, d1 = "2026-10-20", "2026-10-21"
    bars = [_bar("A", d0, 10.0), _bar("A", d1, 11.0)]
    morning = datetime(2026, 10, 21, 10, 0)
    evening = datetime(2026, 10, 21, 16, 0)
    assert [b.close for b in bars_excluding_today(bars, morning)] == [10.0]
    assert [b.close for b in bars_excluding_today(bars, evening)] == [10.0, 11.0]


def test_condition_runner_shares_engine_daily_budget(tmp_path):
    bridge = _bridge(tmp_path)
    orders = ConditionalOrders(bridge.account_db_path)
    exp = (datetime.now() + timedelta(days=2)).isoformat(timespec="minutes")
    ok, msg, oid = orders.create({
        "symbol": "000001", "side": "buy", "cond_type": "price_below",
        "cond_value": 10.0, "value": 5000, "expiry": exp,
        "note": "budget-test"}, min_ticket=0)
    assert ok, msg

    state = EngineState(bridge.account_db_path)
    day = datetime.now().date().isoformat()
    state.bump_budget(day, 1, 0.0)            # 引擎今天已用掉唯一 1 单预算
    runner = ConditionRunner(bridge, orders, enabled=True, max_per_day=1,
                             min_ticket_cny=0, state=state)
    out = runner.tick(quotes={"000001": {"price": 9.9, "prev_close": 10.0,
                                         "change_pct": -1.0}})
    assert out["filled"] == []
    assert out["rejected"] and "预算" in out["rejected"][0]["why"]
    # 预算不足不成交，条件单保持待触发（可明天继续）
    assert any(d["id"] == oid for d in orders.pending(datetime.now()))


def test_condition_runner_engine_off_keeps_order_armed(tmp_path):
    bridge = _bridge(tmp_path)
    orders = ConditionalOrders(bridge.account_db_path)
    exp = (datetime.now() + timedelta(days=2)).isoformat(timespec="minutes")
    ok, msg, oid = orders.create({
        "symbol": "000001", "side": "buy", "cond_type": "price_below",
        "cond_value": 10.0, "value": 5000, "expiry": exp}, min_ticket=0)
    assert ok, msg
    runner = ConditionRunner(bridge, orders, enabled=False, max_per_day=8,
                             min_ticket_cny=0,
                             state=EngineState(bridge.account_db_path))
    out = runner.tick(quotes={"000001": {"price": 9.9, "prev_close": 10.0,
                                         "change_pct": -1.0}})
    assert out["filled"] == [] and out["held"]
    assert any(d["id"] == oid for d in orders.pending(datetime.now()))


def test_refresh_peak_and_held_days_exclude_today(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    b = bridge.broker
    b.submit_order(_order("600519", "buy", 100, 10.0))

    # 峰值：实时/当日高点落库
    now = datetime.now()
    runner = EngineRunner(bridge, cfg={"rules": []})
    runner._refresh_peak("600519", {"price": 11.5, "high": 12.5}, now)
    assert b.get_position("600519").peak_price == pytest.approx(12.5)
    b2 = PaperBroker(bridge.account_db_path, TradingConfig(initial_cash=100000))
    assert b2.get_position("600519").peak_price == pytest.approx(12.5)

    # 持有天数：建仓日之后**已收盘**的交易日数（当日未收盘不计）
    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):                 # noqa: ARG003
            return cls(2026, 10, 21, 10, 0, 0)

    monkeypatch.setattr(runner_mod, "datetime", _FixedDT)
    with sqlite3.connect(bridge.account_db_path) as conn:
        conn.execute("UPDATE positions SET entry_day='2026-10-19'"
                     " WHERE symbol='600519'")
    bridge.data_db.upsert_bars([
        _bar("600519", "2026-10-19", 10.0),
        _bar("600519", "2026-10-20", 10.0),
        _bar("600519", "2026-10-21", 10.0),
    ])
    assert runner._held_days("600519") == 1   # 只有 10-20；10-21 未收盘


# ---------- M1-D 交易时段 / 报价新鲜度 ----------

def test_trading_session_windows():
    cn = rt.CN

    def at(h, m):
        return datetime(2026, 10, 21, h, m, tzinfo=cn)   # 周三

    assert not rt.is_trading_session(at(9, 29))
    assert rt.is_trading_session(at(9, 30))
    assert rt.is_trading_session(at(11, 29))
    assert not rt.is_trading_session(at(11, 30))          # 午休
    assert not rt.is_trading_session(at(12, 30))
    assert rt.is_trading_session(at(13, 0))
    assert rt.is_trading_session(at(14, 59))
    assert not rt.is_trading_session(at(15, 0))


def test_quote_fresh_rejects_stale_and_other_day():
    cn = rt.CN
    now = datetime(2026, 10, 21, 10, 0, tzinfo=cn)
    assert rt.quote_fresh({"ts": (now - timedelta(seconds=60)).isoformat()}, now)
    assert not rt.quote_fresh(
        {"ts": (now - timedelta(seconds=200)).isoformat()}, now)
    assert not rt.quote_fresh({"ts": "2026-10-20T14:59:00"}, now)
    # 节假日：10-01 的报价在 10-08 盘中必须丢弃（不靠节假日表，靠报价日期）
    holiday_now = datetime(2026, 10, 8, 10, 0, tzinfo=cn)
    assert not rt.quote_fresh({"ts": "2026-10-01T10:00:00"}, holiday_now)
    assert rt.quote_fresh({"price": 10.0}, now)           # 无 ts 的合成数据放行


# ---------- M1-E universe / 科创板 / 除权检测 ----------

def test_universe_auto_and_list_validation(tmp_path):
    cfg = {"interval_sec": 60, "universe": "auto", "rules": [
        {"id": "r", "type": "price_level",
         "params": {"op": "below", "value": 10},
         "action": {"side": "buy"}}]}
    ok, msg = validate(cfg)
    assert ok, msg
    assert not validate(dict(cfg, universe="list"))[0]   # list 必须点名

    bridge = _bridge(tmp_path)
    bridge.data_db.add_stock_to_pool("000001")
    runner = EngineRunner(bridge, cfg=cfg)
    assert runner._rule_symbols() == ["000001"]


def test_kcb_buy_requires_200_shares(tmp_path):
    b = PaperBroker(str(tmp_path / "acc.db"), TradingConfig(initial_cash=100000))
    assert b.submit_order(_order("688981", "buy", 100, 50.0)).status.value == \
        "rejected"
    assert b.submit_order(_order("688981", "buy", 200, 50.0)).status.value == \
        "filled"


def test_detect_revisions_and_sync_logs_op(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    bridge.data_db.upsert_bars(_bars("600519", days, [10.0, 10.0, 10.0]))

    # 前复权回溯调整：同一历史交易日的收盘变了（疑似除权除息）
    revised = _bars("600519", days, [10.0, 9.6, 9.5])
    rev = bridge._detect_revisions("600519", revised)
    assert len(rev) == 2 and rev[0]["day"] == "2026-01-06"
    assert bridge._detect_revisions("600519", _bars("600519", days,
                                                    [10.0, 10.0, 10.0])) == []

    # sync_data 检测到后必须落 op_log（不静默）
    import paper_trading.cli as cli_mod

    class _FakeFetcher:
        @staticmethod
        def fetch_daily(sym, start_date=None):      # noqa: ARG004
            return revised

        @staticmethod
        def fetch_stock_names(syms):                # noqa: ARG004
            return {}

    monkeypatch.setattr(cli_mod, "_get_fetcher", lambda: _FakeFetcher)
    res = bridge.sync_data(["600519"])
    assert res["ok"] and res["revisions"][0]["symbol"] == "600519"
    assert any(r["action"] == "data:revision"
               for r in bridge.broker.get_op_log(20))
