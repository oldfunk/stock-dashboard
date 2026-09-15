"""M4a BrokerAdapter 接口 + PaperBroker 桩单测（paper-trading.md §4.5）"""
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


def test_interface_signatures(tmp_db):
    """抽象基类不可实例化；四方法签名与 §4.5 草案一致"""
    with pytest.raises(TypeError):
        BrokerAdapter()
    assert issubclass(PaperBroker, BrokerAdapter)
    sig = inspect.signature(BrokerAdapter.place_order)
    assert list(sig.parameters) == ["self", "code", "direction",
                                    "price", "volume"]
    assert inspect.signature(BrokerAdapter.cancel_order).parameters.keys() \
        == {"self", "order_id"}
    assert "order_id" not in inspect.signature(
        BrokerAdapter.get_positions).parameters
    assert "order_id" not in inspect.signature(
        BrokerAdapter.get_account).parameters
    # 桩实现未穷举：抽象方法在 PaperBroker 全部有实体
    assert not inspect.isabstract(PaperBroker)


def test_import_and_construct_have_no_side_effect(tmp_path, monkeypatch):
    """import + 构造 PaperBroker 不落盘（建表建账只发生在方法调用时）"""
    db_path = str(tmp_path / "untouched.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    import importlib
    import src.paper.broker as broker_mod
    importlib.reload(broker_mod)
    broker_mod.PaperBroker()
    assert not os.path.exists(db_path)


def test_place_cancel_roundtrip(tmp_db):
    """下单→str id→落库 submitted；撤单成功后二次撤返回 False"""
    broker = PaperBroker()
    oid = broker.place_order("600519", "BUY", 1500.0, 100,
                             signal_source="t:BUY")
    assert isinstance(oid, str)
    order = broker._orders.get(int(oid))
    assert order is not None
    assert order["status"] == "submitted"
    assert order["signal_source"] == "t:BUY"
    # 持仓/账户接口调通
    assert broker.get_positions() == []
    acc = broker.get_account()
    assert acc["cash"] == 1000000.0
    # 撤单
    assert broker.cancel_order(oid) is True
    cancelled = broker._orders.get(int(oid))
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert broker.cancel_order(oid) is False  # 已撤不可再撤
    assert broker.cancel_order("999999") is False  # 不存在
    assert broker.cancel_order("not-an-id") is False  # 非法 id


def test_invalid_inputs_rejected_without_write(tmp_db):
    """非法 direction/price/volume 抛 ValueError 且无落库；无 Windows 依赖"""
    broker = PaperBroker()
    with pytest.raises(ValueError):
        broker.place_order("600519", "HOLD", 1500.0, 100)
    with pytest.raises(ValueError):
        broker.place_order("600519", "BUY", 0, 100)
    with pytest.raises(ValueError):
        broker.place_order("600519", "BUY", 1500.0, 0)
    with pytest.raises(ValueError):
        broker.place_order("600519", "SELL", 1500.0, 1.5)
    assert broker._orders.list_by_code("600519") == []
    # 本模块 + 依赖链无 xtquant / Windows-only 引用（import 语句级）
    import re
    import src.paper.broker as broker_mod
    src = inspect.getsource(broker_mod)
    assert not re.search(r"(?m)^\s*(import|from)\s+(xtquant|win32|pywinauto|Qmt)", src)
