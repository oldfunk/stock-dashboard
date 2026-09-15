"""M4a 纸盘五表 schema + DAO 单元测试（paper-trading.md §4.1）"""
import sqlite3

import pytest

from src.models import database as db_mod
from src.models.database import (
    PaperAccountDAO,
    PaperNavDAO,
    PaperOrderDAO,
    PaperPositionDAO,
    PaperTradeDAO,
    init_database,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    init_database()
    return db_path


def test_paper_tables_exist_and_init_idempotent(tmp_db):
    """五表齐备；init_database 重复调用幂等"""
    with sqlite3.connect(tmp_db) as conn:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for t in ("paper_account", "paper_orders", "paper_trades",
              "paper_positions", "paper_nav"):
        assert t in names
    init_database()  # 第二次调用不报错、不丢表
    with sqlite3.connect(tmp_db) as conn:
        names2 = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for t in ("paper_account", "paper_orders", "paper_trades",
              "paper_positions", "paper_nav"):
        assert t in names2


def test_account_get_or_create_and_update(tmp_db):
    """建账默认100万；重复调用保留已有资金；快照更新只写非空字段"""
    dao = PaperAccountDAO()
    assert dao.get() is None
    acc = dao.get_or_create()
    assert acc["id"] == 1
    assert acc["initial_cash"] == 1000000.0
    assert acc["cash"] == 1000000.0
    # 重复建账不覆盖
    dao.update(cash=900000.0)
    acc2 = dao.get_or_create()
    assert acc2["cash"] == 900000.0
    assert acc2["initial_cash"] == 1000000.0
    # 快照更新
    assert dao.update(total_value=1010000.0, cumulative_pnl=10000.0) is True
    acc3 = dao.get()
    assert acc3["cash"] == 900000.0  # 未传字段保持原值
    assert acc3["total_value"] == 1010000.0
    assert acc3["cumulative_pnl"] == 10000.0
    assert dao.update() is False  # 无字段可写


def test_order_trade_flow_with_fees_and_block(tmp_db):
    """委托→成交全链路：费用明细往返；风控拦截 blocked 可落库（§4.4）"""
    orders = PaperOrderDAO()
    trades = PaperTradeDAO()
    oid = orders.place("600519", "BUY", 1500.0, 100,
                       signal_source="run20260915:BUY")
    o = orders.get(oid)
    assert o["status"] == "submitted"
    assert o["volume"] == 100
    assert o["signal_source"] == "run20260915:BUY"
    assert orders.update_status(oid, "filled") is True
    assert orders.get(oid)["status"] == "filled"
    tid = trades.record(oid, "600519", "BUY", 1500.0, 100,
                        commission=37.5, stamp_tax=0, transfer_fee=1.5)
    assert tid is not None
    rows = trades.list_by_code("600519")
    assert len(rows) == 1
    assert rows[0]["order_id"] == oid
    assert rows[0]["commission"] == 37.5
    assert rows[0]["transfer_fee"] == 1.5
    # 风控拦截委托同样落库可查
    oid2 = orders.place("000001", "BUY", 10.0, 100)
    assert orders.update_status(oid2, "blocked") is True
    assert orders.get(oid2)["status"] == "blocked"
    assert len(orders.list_by_code("000001")) == 1


def test_position_nav_roundtrip(tmp_db):
    """持仓 upsert/get/list/remove；净值 save/get_latest"""
    pos = PaperPositionDAO()
    nav = PaperNavDAO()
    assert pos.get("600519") is None
    assert nav.get_latest() is None
    pos.upsert("600519", volume=100, avail_volume=0,
               avg_price=1500.0, floating_pnl=0.0)
    p = pos.get("600519")
    assert (p["volume"], p["avail_volume"], p["avg_price"]) == (100, 0, 1500.0)
    assert p["floating_pnl"] == 0.0
    assert len(pos.list_all()) == 1
    # T+1 解冻：可用数量更新覆盖
    pos.upsert("600519", volume=100, avail_volume=100,
               avg_price=1500.0, floating_pnl=500.0)
    assert pos.get("600519")["avail_volume"] == 100
    nav.save("2026-09-15", total_value=1010000.0, cash=860000.0,
             market_value=150000.0, pnl=10000.0, cumulative_pnl=10000.0)
    latest = nav.get_latest()
    assert latest["nav_date"] == "2026-09-15"
    assert latest["total_value"] == 1010000.0
    assert nav.get("2026-09-15")["cash"] == 860000.0
    assert pos.remove("600519") is True
    assert pos.get("600519") is None
    assert pos.remove("600519") is False  # 重复删除返回 False
