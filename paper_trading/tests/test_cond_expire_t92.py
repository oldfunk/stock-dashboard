"""T92：意图跨日过期时，条件单必须一起收口（不再永久 queued_rq）。"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.broker.paper_broker import PaperBroker  # noqa: E402
from paper_trading.engine.conditional import ConditionalOrders  # noqa: E402
from paper_trading.integration.rq_engine import queue as q  # noqa: E402
from paper_trading.models import TradingConfig  # noqa: E402
from paper_trading.utils import daily_check as dc  # noqa: E402


def _acct(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=100000.0))


def _cond(db) -> int:
    co = ConditionalOrders(str(db))
    oid = co.create({"symbol": "000001", "side": "buy",
                     "cond_type": "price_below", "cond_value": 10.0,
                     "value": 20000})[2]
    co.mark(oid, "queued_rq", 9.95)
    return oid


def test_expire_stale_closes_the_queued_condition(tmp_path):
    b = _acct(tmp_path)
    db = str(b.db_path)
    oid = _cond(db)
    biz = q.enqueue(db, "000001", "buy", 2000, rule_ref=f"cond:{oid}")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE rq_intents SET created_at='2026-11-20T10:00:00'"
                  " WHERE biz_id=?", (biz,))
    expired = q.expire_stale(db, "2026-11-21")
    assert expired == [biz]
    co = ConditionalOrders(db)
    row = [r for r in co.list_all(10) if r["id"] == oid][0]
    assert row["status"] == "expired"
    detail = row["detail"] or ""
    assert "条件单作废" in detail and "2026-11-21" in detail


def test_expire_stale_leaves_armed_condition_alone(tmp_path):
    """没触发入队的 armed 条件没有意图，不该被 expire_stale 误伤（由 expire_due 管）。"""
    b = _acct(tmp_path)
    db = str(b.db_path)
    co = ConditionalOrders(db)
    oid = co.create({"symbol": "000001", "side": "buy",
                     "cond_type": "price_below", "cond_value": 10.0,
                     "value": 20000})[2]
    q.expire_stale(db, "2026-11-21")
    row = [r for r in co.list_all(10) if r["id"] == oid][0]
    assert row["status"] == "armed"


def test_expire_stale_non_cond_intent_untouched(tmp_path):
    b = _acct(tmp_path)
    db = str(b.db_path)
    biz = q.enqueue(db, "000001", "buy", 2000, rule_ref="engine")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE rq_intents SET created_at='2026-11-20T10:00:00'"
                  " WHERE biz_id=?", (biz,))
    assert q.expire_stale(db, "2026-11-21") == [biz]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT status FROM rq_intents WHERE biz_id=?",
                         (biz,)).fetchone()[0] == "expired"


def test_cond_loop_warns_on_old_zombie_without_today_trigger(tmp_path):
    b = _acct(tmp_path)
    db = str(b.db_path)
    q.expire_stale(db, date.today().isoformat())   # 建 rq_intents 表
    oid = _cond(db)
    old = (date.today() - timedelta(days=5)).isoformat()
    with sqlite3.connect(db) as c:
        c.execute("UPDATE conditional_orders SET created_at=? WHERE id=?",
                  (old + "T09:30:00", oid))
    it = dc.check_cond_loop(db, date.today().isoformat(), executor="rqalpha")
    assert it["status"] == "warn" and str(oid) in it["detail"]


def test_cond_loop_green_without_zombies(tmp_path):
    b = _acct(tmp_path)
    db = str(b.db_path)
    q.expire_stale(db, date.today().isoformat())   # 建 rq_intents 表
    ConditionalOrders(db)                          # 建 conditional_orders 表
    it = dc.check_cond_loop(db, date.today().isoformat(), executor="rqalpha")
    assert it["status"] == "green"
