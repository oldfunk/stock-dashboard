"""Phase1-4 回归单测（离线，不依赖 akshare/网络）。"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.data.db_manager import DataDBManager
from paper_trading.models import Bar, Order, OrderType, Signal, SignalType, TradingConfig
from paper_trading.risk.risk_manager import RiskManager
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy
from paper_trading.utils.trading_calendar import is_trading_day, next_trading_day


def mkbar(sym, day, close):
    return Bar(symbol=sym, timestamp=datetime(2024, 1, 1) + timedelta(days=day),
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def test_get_bars_returns_latest():
    f = tempfile.mktemp(suffix=".db")
    db = DataDBManager(f)
    bars = [mkbar("600519", i, 10 + i) for i in range(50)]
    db.upsert_bars(bars)
    got = db.get_bars("600519", limit=30)
    assert len(got) == 30
    assert got[0].close == 30.0 and got[-1].close == 59.0
    Path(f).unlink(missing_ok=True)


def test_signal_only_on_last_cross():
    s = MACrossStrategy(5, 20)
    # 构造最后一根金叉
    closes = [10] * 20 + [9, 9, 9, 9, 20]
    bl = [mkbar("X", i, c) for i, c in enumerate(closes)]
    sigs = s.generate_signals({"X": bl})
    assert "X" in sigs and sigs["X"].direction == SignalType.BUY
    # 再加一根无交叉 → 应无信号（去重）
    bl2 = bl + [mkbar("X", len(bl), 20)]
    assert s.generate_signals({"X": bl2}) == {}


def test_t1_calendar():
    assert not is_trading_day(datetime(2026, 9, 19))  # 周六（固定日期断言，与今天无关）
    assert next_trading_day(datetime(2026, 9, 18)).isoformat() == "2026-09-21"  # 周五→周一
    f = tempfile.mktemp(suffix=".db")
    b = PaperBroker(f, TradingConfig(initial_cash=100000))
    b.submit_order(Order(symbol="600519", direction=1, volume=100,
                         order_type=OrderType.LIMIT, limit_price=10.0))
    assert b.get_position("600519").available_volume == 0
    # 日期无关：解冻日 = 下单日的下一交易日（ broker 内部用 datetime.now() 落 freeze_date）
    expect = next_trading_day(datetime.now())
    n = b.unfreeze_t1(date=expect)
    assert n == 1 and b.get_position("600519").available_volume == 100
    Path(f).unlink(missing_ok=True)


def test_rejected_persisted_and_volume_rule():
    f = tempfile.mktemp(suffix=".db")
    b = PaperBroker(f, TradingConfig(initial_cash=100000))
    r = b.submit_order(Order(symbol="600519", direction=1, volume=50,
                             order_type=OrderType.LIMIT, limit_price=10.0))
    assert r.status.value == "rejected"
    hist = b.get_order_history(5)
    assert hist[0]["status"] == "rejected"
    Path(f).unlink(missing_ok=True)


def test_limit_band_and_avg_cost_with_fees():
    f = tempfile.mktemp(suffix=".db")
    b = PaperBroker(f, TradingConfig(initial_cash=1000000))
    r = b.submit_order(Order(symbol="600519", direction=1, volume=100,
                             order_type=OrderType.LIMIT, limit_price=100.0, prev_close=80.0))
    assert r.status.value == "rejected"
    b.submit_order(Order(symbol="600519", direction=1, volume=100,
                         order_type=OrderType.LIMIT, limit_price=10.0))
    assert b.get_position("600519").avg_cost > 10.0  # 含佣金
    fills = b.get_fill_history(1)
    assert fills[0]["price"] == round(fills[0]["price"], 2)  # 最小变动价位 0.01
    Path(f).unlink(missing_ok=True)


def test_risk_multi_price_and_drawdown():
    rm = RiskManager(max_single_order_value=1e9, max_drawdown_pct=0.2)
    poss = {"A": Signal("A", SignalType.BUY, 100, 10.0)}
    from paper_trading.models import Position
    positions = {"AAA": Position("AAA", 1000, 1000, 10.0, datetime.now()),
                 "BBB": Position("BBB", 1000, 1000, 10.0, datetime.now())}
    sig = Signal(symbol="CCC", direction=SignalType.BUY, volume=100, price=10.0)
    ok, _ = rm.check_signal(sig, 10.0, 1e6, positions, 100000.0,
                            prices={"AAA": 10.0, "BBB": 10.0, "CCC": 10.0})
    assert ok
    rm.update_peak(100.0)
    halted, dd = rm.check_drawdown(79.0)
    assert halted and dd > 0.2


def test_stock_name_cache_roundtrip():
    f = tempfile.mktemp(suffix=".db")
    db = DataDBManager(f)
    db.add_stock_to_pool("600519")
    db.upsert_stock_names({"600519": "贵州茅台", "000858": "五粮液"})
    names = db.get_stock_names()
    assert names == {"600519": "贵州茅台", "000858": "五粮液"}
    # 空名称的 upsert 不应清掉已有名称
    db.upsert_stock_names({"600519": ""})
    assert db.get_stock_names()["600519"] == "贵州茅台"
    assert db.get_pool_symbols() == sorted(db.get_pool_symbols())
    Path(f).unlink(missing_ok=True)


def test_op_log_roundtrip():
    import json

    f = tempfile.mktemp(suffix=".db")
    b = PaperBroker(f, TradingConfig(initial_cash=100000))
    b.log_operation("buy", {"symbol": "600519"}, True,
                    {"status": "filled", "filled_price": 10.0}, 90000.0, 100000.0)
    b.log_operation("sell", {"symbol": "600519"}, False,
                    {"error": "Insufficient available volume"}, 90000.0, 100000.0)
    rows = b.get_op_log(10)
    assert len(rows) == 2
    assert rows[0]["action"] == "sell" and rows[0]["ok"] == 0  # 倒序
    assert json.loads(rows[1]["result"])["status"] == "filled"
    assert rows[1]["cash_after"] == 90000.0
    Path(f).unlink(missing_ok=True)


def _fake_bridge(monkeypatch, tmp, bars):
    import paper_trading.cli as hb

    class FakeFetcher:
        @staticmethod
        def fetch_daily(symbol, start_date=None, end_date=None, adjust="qfq", **kw):
            return bars

        @staticmethod
        def fetch_stock_names(symbols):
            return {"600519": "贵州茅台"}

    monkeypatch.setattr(hb, "_get_fetcher", lambda: FakeFetcher)
    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    return hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml")


def test_sync_data_with_fake_fetcher(monkeypatch, tmp_path):
    b = _fake_bridge(monkeypatch, str(tmp_path),
                     [mkbar("600519", i, 10 + i) for i in range(5)])
    res = b.sync_data(["600519"])
    assert res["ok"] and res["updated"] == {"600519": 5}
    assert b.data_db.get_data_asof() == "2024-01-05"
    assert b.get_status()["data_asof"] == "2024-01-05"


def test_run_daily_skips_without_fresh_bars(monkeypatch, tmp_path):
    b = _fake_bridge(monkeypatch, str(tmp_path), [])  # 源端无新数据
    b.data_db.upsert_bars([mkbar("600519", i, 10 + i) for i in range(30)])  # 旧数据
    res = b.run_daily(["600519"])
    assert res.get("skipped") == "no-fresh-bars"
    assert res["orders_executed"] == 0
