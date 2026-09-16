"""M4a PaperBroker 撮合引擎单测"""
import inspect
import os

import pytest

from src.models import database as db_mod
from src.models.database import init_database
from src.paper.broker import BrokerAdapter, PaperBroker


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    init_database()
    return db_path


@pytest.fixture
def broker(tmp_db):
    return PaperBroker()


# ---- 接口签名 ----

def test_interface_signatures(tmp_db):
    with pytest.raises(TypeError):
        BrokerAdapter()
    assert issubclass(PaperBroker, BrokerAdapter)
    sig = inspect.signature(BrokerAdapter.place_order)
    assert list(sig.parameters) == ["self", "code", "direction",
                                    "price", "volume"]
    assert inspect.signature(BrokerAdapter.cancel_order).parameters.keys() \
        == {"self", "order_id"}
    assert not inspect.isabstract(PaperBroker)


def test_import_and_construct_have_no_side_effect(tmp_path, monkeypatch):
    db_path = str(tmp_path / "untouched.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    import importlib
    import src.paper.broker as broker_mod
    importlib.reload(broker_mod)
    broker_mod.PaperBroker()
    assert not os.path.exists(db_path)


# ---- place_order ----

def test_place_cancel_roundtrip(broker):
    oid = broker.place_order("600519", "BUY", 1500.0, 100,
                             signal_source="t:BUY")
    assert isinstance(oid, str)
    order = broker._orders.get(int(oid))
    assert order is not None
    assert order["status"] == "submitted"
    assert order["signal_source"] == "t:BUY"
    assert broker.get_positions() == []
    acc = broker.get_account()
    assert acc["cash"] == 1000000.0
    assert broker.cancel_order(oid) is True
    cancelled = broker._orders.get(int(oid))
    assert cancelled["status"] == "cancelled"
    assert broker.cancel_order(oid) is False
    assert broker.cancel_order("999999") is False
    assert broker.cancel_order("not-an-id") is False


def test_invalid_inputs_rejected_without_write(broker):
    with pytest.raises(ValueError):
        broker.place_order("600519", "HOLD", 1500.0, 100)
    with pytest.raises(ValueError):
        broker.place_order("600519", "BUY", 0, 100)
    with pytest.raises(ValueError):
        broker.place_order("600519", "BUY", 1500.0, 0)
    with pytest.raises(ValueError):
        broker.place_order("600519", "SELL", 1500.0, 1.5)
    assert broker._orders.list_by_code("600519") == []


def test_volume_not_100_lot_rejected(broker):
    """非 100 整数倍 volume 被拒"""
    with pytest.raises(ValueError, match="100 整数倍"):
        broker.place_order("600519", "BUY", 1500.0, 150)
    with pytest.raises(ValueError, match="100 整数倍"):
        broker.place_order("600519", "BUY", 1500.0, 99)


def test_sell_exceeds_available_rejected(broker):
    """卖出数量超过可用持仓被拒"""
    with pytest.raises(ValueError, match="超过可用持仓"):
        broker.place_order("600519", "SELL", 1500.0, 100)


# ---- fill_order ----

def test_fill_buy_basic(broker):
    """买入撮合：费用计算 + 持仓更新 + 资金扣减"""
    oid = broker.place_order("600519", "BUY", 100.0, 500)
    result = broker.fill_order(int(oid), 100.0)

    # 成交价 = 委托价 × (1 + slippage)，slippage 默认 0.1%
    assert result["fill_price"] == 100.1  # 100 * 1.001
    # 佣金: max(100.1*500*0.00025=12.51, 5) = 12.51
    assert result["commission"] == 12.51
    assert result["stamp_tax"] == 0.0     # 买入无印花税
    assert result["transfer_fee"] == round(100.1 * 500 * 0.00001, 2)  # 0.50

    # 持仓：T+1 冻结，avail=0
    pos = broker._positions.get("600519")
    assert pos is not None
    assert pos["volume"] == 500
    assert pos["avail_volume"] == 0  # T+1 冻结
    assert pos["avg_price"] == 100.1

    # 资金
    acc = broker._account.get()
    total_cost = 100.1 * 500 + result["total_cost"]
    assert acc["cash"] == round(1000000.0 - total_cost, 2)


def test_fill_sell_basic(broker):
    """卖出撮合：费用 + 持仓清仓"""
    # 先买入
    oid_buy = broker.place_order("600519", "BUY", 100.0, 500)
    broker.fill_order(int(oid_buy), 100.0)

    # 解冻 T+1
    broker.end_of_day("2026-09-15")

    # 卖出
    oid_sell = broker.place_order("600519", "SELL", 100.0, 500)
    result = broker.fill_order(int(oid_sell), 100.0)

    # 卖出有印花税
    assert result["stamp_tax"] > 0
    # 佣金: max(fill_price*500*0.00025, 5) > 5
    assert result["commission"] > 5.0

    # 持仓清空
    pos = broker._positions.get("600519")
    assert pos is None


def test_fill_sell_partial(broker):
    """部分卖出"""
    oid_buy = broker.place_order("600519", "BUY", 100.0, 500)
    broker.fill_order(int(oid_buy), 100.0)
    broker.end_of_day("2026-09-15")

    oid_sell = broker.place_order("600519", "SELL", 100.0, 300)
    broker.fill_order(int(oid_sell), 100.0)

    pos = broker._positions.get("600519")
    assert pos["volume"] == 200
    assert pos["avail_volume"] == 200


def test_fill_non_submitted_rejected(broker):
    """非 submitted 状态的订单不可撮合"""
    oid = broker.place_order("600519", "BUY", 100.0, 500)
    broker.cancel_order(oid)
    with pytest.raises(ValueError, match="非 submitted"):
        broker.fill_order(int(oid), 100.0)


def test_fill_invalid_order_rejected(broker):
    """不存在的订单"""
    with pytest.raises(ValueError, match="不存在"):
        broker.fill_order(999999, 100.0)


def test_fill_zero_price_rejected(broker):
    """市价为 0"""
    oid = broker.place_order("600519", "BUY", 100.0, 500)
    with pytest.raises(ValueError, match="市价须"):
        broker.fill_order(int(oid), 0)


def test_fee_calculation_sell(broker):
    """卖出费用：佣金 + 印花税 + 过户费"""
    oid = broker.place_order("600519", "BUY", 100.0, 500)
    broker.fill_order(int(oid), 100.0)
    broker.end_of_day("2026-09-15")

    oid_sell = broker.place_order("600519", "SELL", 100.0, 500)
    result = broker.fill_order(int(oid_sell), 100.0)

    amount = result["fill_price"] * 500
    # 佣金: max(amount * 0.00025, 5)
    assert result["commission"] == max(round(amount * 0.00025, 2), 5.0)
    # 印花税: amount * 0.005
    assert result["stamp_tax"] == round(amount * 0.005, 2)
    # 过户费: amount * 0.00001
    assert result["transfer_fee"] == round(amount * 0.00001, 2)


def test_fee_minimum_commission(broker):
    """佣金最低 5 元"""
    oid = broker.place_order("600519", "BUY", 10.0, 100)
    result = broker.fill_order(int(oid), 10.0)
    # 10 * 100 = 1000, commission = max(1000*0.00025, 5) = 5
    assert result["commission"] == 5.0


# ---- T+1 ----

def test_t1_freeze_and_unfreeze(broker):
    """T+1 冻结：买入当日不可卖，次日解冻"""
    oid = broker.place_order("600519", "BUY", 100.0, 500)
    broker.fill_order(int(oid), 100.0)

    # 当日：不可卖
    with pytest.raises(ValueError, match="超过可用持仓"):
        broker.place_order("600519", "SELL", 100.0, 500)

    # 日终处理：解冻
    broker.end_of_day("2026-09-15")

    # 次日：可卖
    pos = broker._positions.get("600519")
    assert pos["avail_volume"] == 500

    oid_sell = broker.place_order("600519", "SELL", 100.0, 500)
    assert oid_sell is not None


def test_t1_multiple_buys(broker):
    """多次买入 T+1 独立冻结"""
    oid1 = broker.place_order("600519", "BUY", 100.0, 500)
    broker.fill_order(int(oid1), 100.0)
    broker.end_of_day("2026-09-15")

    # 第二天再买
    oid2 = broker.place_order("600519", "BUY", 100.0, 300)
    broker.fill_order(int(oid2), 100.0)

    pos = broker._positions.get("600519")
    assert pos["volume"] == 800
    assert pos["avail_volume"] == 500  # 只有第一天的 500 可卖

    # 卖 500 可以
    oid_sell = broker.place_order("600519", "SELL", 100.0, 500)
    assert oid_sell is not None

    # 卖 600 不行
    with pytest.raises(ValueError, match="超过可用持仓"):
        broker.place_order("600519", "SELL", 100.0, 600)


# ---- 风控 ----

def test_risk_max_single_position(broker):
    """单股仓位超 20% 被拦"""
    # 100 万，单股上限 20% = 20 万
    # 买 2500 股 × 100 元 = 25 万 > 20 万
    oid = broker.place_order("600519", "BUY", 100.0, 2500)
    with pytest.raises(ValueError, match="单股.*仓位"):
        broker.fill_order(int(oid), 100.0)


def test_risk_max_total_position(broker):
    """总仓位超 80% 被拦"""
    # 买 4 只各 19%：单股不触发上限，总仓位 76%
    for code in ["000001", "000002", "000003", "000004"]:
        oid = broker.place_order(code, "BUY", 100.0, 1900)
        broker.fill_order(int(oid), 100.0)
        broker.end_of_day("2026-09-15")

    # 再买 5% → 总仓位约 81%，触发总仓位限制
    oid = broker.place_order("000005", "BUY", 100.0, 500)
    with pytest.raises(ValueError, match="总仓位"):
        broker.fill_order(int(oid), 100.0)


# ---- end_of_day ----

def test_end_of_day_nav(broker):
    """日终处理：净值记录"""
    oid = broker.place_order("600519", "BUY", 100.0, 1000)
    broker.fill_order(int(oid), 100.0)

    result = broker.end_of_day("2026-09-15")
    assert result["nav_date"] == "2026-09-15"
    assert result["total_value"] > 0
    assert result["cash"] < 1000000.0
    assert result["cumulative_pnl"] < 0  # 花了钱，亏了手续费

    # 再次调用幂等
    result2 = broker.end_of_day("2026-09-15")
    assert result2["total_value"] == result["total_value"]


# ---- 无 Windows 依赖 ----

def test_no_windows_dependency():
    """本模块 + 依赖链无 Windows-only 引用"""
    import re
    import src.paper.broker as broker_mod
    src = inspect.getsource(broker_mod)
    assert not re.search(r"(?m)^\s*(import|from)\s+(xtquant|win32|pywinauto|Qmt)", src)
