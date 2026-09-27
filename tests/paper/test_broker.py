"""纸盘撮合/风控/日历单测（行为对标子项目 test_core，不读 ambient）。"""
from datetime import date

import pytest

from src.paper.broker import PaperBroker
from src.paper.calendar import is_trading_day, next_trading_day
from src.paper.risk import RiskManager
from src.paper.types import Order, OrderStatus, OrderType, Signal, SignalType, TradingConfig


@pytest.fixture
def broker(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "paper.db"),
                       config=TradingConfig())


def _buy(symbol="600519", volume=100, price=10.0):
    return Order(symbol=symbol, direction=1, volume=volume,
                 order_type=OrderType.LIMIT, limit_price=price)


def _sell(symbol="600519", volume=100, price=10.0):
    return Order(symbol=symbol, direction=-1, volume=volume,
                 order_type=OrderType.LIMIT, limit_price=price)


class TestBroker:
    def test_buy_sell_roundtrip_costs(self, broker):
        broker.set_trade_date("2026-09-21")  # 周一
        o = broker.submit_order(_buy(price=10.0))
        assert o.status == OrderStatus.FILLED
        # 买入执行价 10.01（含滑点），费用：佣金 5 + 过户 0.01001
        assert abs(broker.get_cash() - (100000 - 1006.01001)) < 0.01
        pos = broker.get_position("600519")
        assert pos.total_volume == 100 and pos.available_volume == 0
        assert abs(pos.avg_cost - 10.0601001) < 1e-6
        # T+1：次日解冻
        assert broker.unfreeze_t1("2026-09-22") == 1
        assert broker.get_position("600519").available_volume == 100
        broker.set_trade_date("2026-09-22")
        s = broker.submit_order(_sell(price=10.0))
        assert s.status == OrderStatus.FILLED
        # 卖出执行价 9.99，净得 999-5-0.4995-0.00999
        assert abs(broker.get_cash() - (100000 - 1006.01001 + 993.49051)) < 0.01
        assert broker.get_position("600519") is None

    def test_t1_weekend_skip(self, broker):
        broker.set_trade_date("2026-09-25")  # 周五
        broker.submit_order(_buy())
        assert broker.unfreeze_t1("2026-09-26") == 0  # 周六不解
        assert broker.unfreeze_t1("2026-09-28") == 1  # 下周一解
        assert broker.get_position("600519").available_volume == 100

    def test_rejects(self, broker):
        assert broker.submit_order(_buy(volume=50)).status == OrderStatus.REJECTED
        assert broker.submit_order(_buy(volume=0)).status == OrderStatus.REJECTED
        o = _buy(price=11.5)
        o.prev_close = 10.0
        assert broker.submit_order(o).status == OrderStatus.REJECTED  # 涨停外
        assert broker.submit_order(_buy(volume=100000)).status == OrderStatus.REJECTED  # 没钱
        assert broker.submit_order(_sell()).status == OrderStatus.REJECTED  # 没仓
        rows = broker.get_order_history(limit=10)
        assert all(r["status"] == "rejected" for r in rows)
        assert len(rows) == 5

    def test_avg_cost_with_fees(self, broker):
        broker.set_trade_date("2026-09-21")
        broker.submit_order(_buy(volume=100, price=10.0))
        broker.set_trade_date("2026-09-22")
        broker.submit_order(_buy(volume=100, price=20.0))
        pos = broker.get_position("600519")
        assert pos.total_volume == 200
        assert abs(pos.avg_cost - (1006.01001 + 2006.02001) / 200) < 1e-6

    def test_limit_bands(self):
        assert PaperBroker.limit_pct_for("688001") == 0.20
        assert PaperBroker.limit_pct_for("300001") == 0.20
        assert PaperBroker.limit_pct_for("800001") == 0.30
        assert PaperBroker.limit_pct_for("600519") == 0.10

    def test_nav_and_history(self, broker):
        broker.set_trade_date("2026-09-21")
        broker.submit_order(_buy(price=10.0))
        snap = broker.get_nav({"600519": 10.0})
        assert abs(snap.total_value - (98993.98999 + 100 * 10.0)) < 0.01
        assert snap.pnl < 0  # 费用必然微亏
        broker.record_nav(snap)
        hist = broker.get_nav_history()
        assert len(hist) == 1 and hist[0]["total_value"] == snap.total_value

    def test_trade_date_override(self, broker):
        broker.set_trade_date("2026-09-21")
        assert broker._today().isoformat() == "2026-09-21"
        broker.clear_trade_date()
        assert broker._today() == date.today()


class TestRisk:
    def test_single_order_cap(self):
        r = RiskManager()
        s = Signal(symbol="600519", direction=SignalType.BUY, volume=100000)
        ok, _ = r.check_signal(s, 10.0, 10**9, {}, 10**9)
        assert ok is False

    def test_position_pct(self):
        from src.paper.types import Position
        from datetime import datetime
        r = RiskManager(max_position_pct=0.3)
        pos = {"600519": Position("600519", 10000, 10000, 10.0, datetime.now())}
        s = Signal(symbol="600519", direction=SignalType.BUY, volume=10000)
        ok, _ = r.check_signal(s, 100.0, 10**7, pos, 10**6)
        assert ok is False

    def test_drawdown(self):
        r = RiskManager(max_drawdown_pct=0.20)
        r.update_peak(100.0)
        assert r.check_drawdown(75.0)[0] is True
        assert r.check_drawdown(85.0)[0] is False


class TestCalendar:
    def test_weekend(self):
        assert is_trading_day("2026-09-25") is True   # 周五
        assert is_trading_day("2026-09-26") is False  # 周六
        assert next_trading_day("2026-09-25").isoformat() == "2026-09-28"

    def test_holidays(self):
        assert is_trading_day("2026-09-28", {"2026-09-28"}) is False
