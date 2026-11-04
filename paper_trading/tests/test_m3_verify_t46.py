"""T46 `m3 verify` 的检查项测试：每项都要"红了会响"，钱类红必须能触发回退标志。

用真实表结构（PaperBroker 建表）+ 直接插行，不依赖网络/rqalpha。
"""
import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.data.db_manager import DataDBManager
from paper_trading.integration import mother as mother_mod
from paper_trading.models import Bar, TradingConfig
from paper_trading.utils import m3_verify as mv

TODAY = "2026-10-29"
T = date(2026, 10, 29)


@pytest.fixture()
def broker(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=100000.0))


@pytest.fixture()
def conn(broker):
    c = sqlite3.connect(str(broker.db_path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def add_fill(c, symbol="000651", direction=1, volume=2000, price=37.58,
             commission=18.79, stamp=0.0, transfer=0.7516, ts=None):
    c.execute("""INSERT INTO fills (order_id, symbol, direction, volume, price,
                 commission, stamp_duty, transfer_fee, timestamp)
                 VALUES (1,?,?,?,?,?,?,?,?)""",
              (symbol, direction, volume, price, commission, stamp, transfer,
               (ts or f"{TODAY}T15:00:00")))
    c.commit()


def add_nav(c, cash=50000.0, mv_=40000.0, total=None, ts=None):
    c.execute("""INSERT INTO nav_history (timestamp, cash, market_value, total_value,
                 available_cash, pnl, pnl_pct) VALUES (?,?,?,?,?,?,?)""",
              (ts or f"{TODAY}T15:30:00", cash, mv_,
               total if total is not None else cash + mv_, cash, 0.0, 0.0))
    c.commit()


def add_oplog(c, action="parity:diff", ok=1, ts=None, result=None, params=None):
    c.execute("""INSERT INTO op_log (timestamp, action, params, ok, result,
                 cash_after, total_value_after) VALUES (?,?,?,?,?,?,?)""",
              (ts or f"{TODAY}T15:45:00", action,
               json.dumps(params or {}, ensure_ascii=False), ok,
               json.dumps(result or {}, ensure_ascii=False), None, None))
    c.commit()


# ---------- 费用三件套 ----------
def test_fees_green_when_rates_match(conn):
    add_fill(conn)                      # 75,160：佣金 18.79 + 过户费 0.7516
    r = mv.check_fees(conn, TradingConfig(), TODAY)
    assert r["status"] == "green", r["detail"]
    assert r["money"] is True


def test_fees_red_when_commission_uses_framework_rate(conn):
    """佣金按万八算（就是 T38 之前的错法）必须红，并触发回退标志。"""
    add_fill(conn, commission=75160 * 0.0008)
    r = mv.check_fees(conn, TradingConfig(), TODAY)
    assert r["status"] == "red" and r["money"] and r["on_red"] == "回退"
    assert "commission" in r["detail"]


def test_fees_red_when_sell_stamp_missing(conn):
    amount = 2000 * 37.58
    add_fill(conn, direction=-1, commission=amount * 0.00025, stamp=0.0,
             transfer=amount * 0.00001)
    r = mv.check_fees(conn, TradingConfig(), TODAY)
    assert r["status"] == "red" and "stamp_duty" in r["detail"]


def test_fees_floor_applies_on_small_ticket(conn):
    add_fill(conn, volume=100, price=10.0, commission=5.0, transfer=0.01)
    assert mv.check_fees(conn, TradingConfig(), TODAY)["status"] == "green"


def test_fees_warn_when_no_fills(conn):
    r = mv.check_fees(conn, TradingConfig(), TODAY)
    assert r["status"] == "warn"           # 0 笔不许报成"通过"


# ---------- T+1 ----------
def test_t_plus_one_red_when_today_buy_is_sellable(conn):
    conn.execute("""INSERT INTO positions (symbol, total_volume, available_volume,
                    avg_cost, last_update, entry_day, peak_price)
                    VALUES ('000651', 2000, 2000, 37.58, ?, ?, 0)""",
                 (f"{TODAY}T15:00:00", TODAY))
    conn.commit()
    r = mv.check_t_plus_one(conn, TODAY)
    assert r["status"] == "red" and r["money"] and "当日买入可卖" in r["detail"]


def test_t_plus_one_green_and_skips_missing_entry_day(conn):
    conn.execute("""INSERT INTO positions (symbol, total_volume, available_volume,
                    avg_cost, last_update, entry_day, peak_price)
                    VALUES ('000651', 2000, 0, 37.58, ?, ?, 0)""",
                 (f"{TODAY}T15:00:00", TODAY))
    conn.commit()
    assert mv.check_t_plus_one(conn, TODAY)["status"] == "green"


# ---------- NAV ----------
def test_nav_red_when_today_missing(conn):
    add_nav(conn, ts="2026-10-28T15:30:00")
    r = mv.check_nav(conn, TODAY)
    assert r["status"] == "red" and "没有 NAV 行" in r["detail"]


def test_nav_red_when_duplicated(conn):
    add_nav(conn)
    add_nav(conn)
    assert "恰 1 行" not in mv.check_nav(conn, TODAY)["detail"]
    assert mv.check_nav(conn, TODAY)["status"] == "red"


def test_nav_red_when_total_not_self_consistent(conn):
    add_nav(conn, cash=50000.0, mv_=40000.0, total=95000.0)
    r = mv.check_nav(conn, TODAY)
    assert r["status"] == "red" and "total≠cash+市值" in r["detail"]


def test_nav_green(conn):
    add_nav(conn)
    assert mv.check_nav(conn, TODAY)["status"] == "green"


# ---------- 双活检测 ----------
def test_duplicate_fills_red_when_both_lines_executed(conn):
    add_fill(conn)
    add_fill(conn)                        # 同 symbol/价/量/分钟 两行
    r = mv.check_duplicate_fills(conn, TODAY)
    assert r["status"] == "red" and "双活" in r["detail"]


def test_duplicate_fills_green_when_distinct(conn):
    add_fill(conn, volume=2000)
    add_fill(conn, symbol="000858", volume=300)
    assert mv.check_duplicate_fills(conn, TODAY)["status"] == "green"


# ---------- 影子留痕 / 审计 / 计划 / 条件单 ----------
def test_parity_trace_warn_when_absent(conn):
    assert mv.check_parity_trace(conn, TODAY)["status"] == "warn"


def test_parity_trace_red_on_nonzero_delta(conn):
    add_oplog(conn, result={"cash_delta": 0.0, "fee_delta": -2.2, "fill_delta": 0})
    assert mv.check_parity_trace(conn, TODAY)["status"] == "red"


def test_parity_trace_red_when_ok_is_zero(conn):
    add_oplog(conn, ok=0, result={"cash_delta": 0.0, "fee_delta": 0.0, "fill_delta": 0})
    assert mv.check_parity_trace(conn, TODAY)["status"] == "red"


def test_parity_trace_green_when_zero_delta(conn):
    add_oplog(conn, result={"cash_delta": 0.0, "fee_delta": 0.0, "fill_delta": 0})
    assert mv.check_parity_trace(conn, TODAY)["status"] == "green"


def test_audit_trail_red_when_fills_have_no_ops(conn):
    add_fill(conn)
    r = mv.check_audit_trail(conn, TODAY)
    assert r["status"] == "red" and "没有任何执行" in r["detail"]


def test_audit_trail_green_with_engine_sell_trace(conn):
    add_fill(conn)
    add_oplog(conn, action="engine:sell", params={"symbol": "000651"})
    assert mv.check_audit_trail(conn, TODAY)["status"] == "green"


def test_plan_chain_warn_when_no_plan_today(conn):
    conn.execute("""INSERT INTO agent_plans (plan_date, symbols, plan_json, status,
                    created_at) VALUES (?,?,?,?,?)""",
                 ("2026-10-28", '["000651"]', "{}", "executed", f"{TODAY}T00:00:00"))
    conn.commit()
    assert mv.check_plan_chain(conn, TODAY)["status"] == "warn"


def test_cond_chain_skips_without_table(conn):
    """`conditional_orders` 由条件单模块建表，账本新库里没有它 ⇒ 只能 skip，不许报绿。"""
    r = mv.check_conditional_chain(conn, TODAY)
    assert r["status"] == "skip" and "不存在" in r["detail"]


# ---------- 心跳 ----------
def test_heartbeat_red_when_due_and_stale(tmp_path):
    from paper_trading.utils import heartbeat as hb
    p = tmp_path / "heartbeat.json"
    hb.touch("engine", path=p, now=datetime(2026, 10, 29, 9, 0))
    r = mv.check_heartbeat(path=p, now=datetime(2026, 10, 29, 10, 0))   # 盘中，已 1h
    assert r["status"] == "red" and "engine" in r["detail"]


def test_heartbeat_not_judged_outside_window(tmp_path):
    from paper_trading.utils import heartbeat as hb
    p = tmp_path / "heartbeat.json"
    hb.touch("engine", path=p, now=datetime(2026, 10, 29, 9, 0))
    r = mv.check_heartbeat(path=p, now=datetime(2026, 10, 29, 3, 0))
    assert r["status"] == "warn" and "不在应跳时段" in r["detail"]


# ---------- 行情闸（走 T41 的逐日台账）----------
@pytest.fixture()
def data_db(tmp_path, monkeypatch):
    db = DataDBManager(tmp_path / "data.db")
    bars = [Bar(symbol="000651", timestamp=datetime(2026, 10, d, 15, 0), open=37.5,
                high=37.7, low=37.4, close=37.58, volume=1000, amount=37580.0,
                adjust="raw") for d in (27, 28)]
    db.upsert_raw_bars(bars, source="t46")
    monkeypatch.setattr(mother_mod, "raw_close_map",
                        lambda codes, days=30, mother=None, ttl=0.0, since=None:
                        {str(c): {"2026-10-27": {"close_raw": 37.58},
                                  "2026-10-28": {"close_raw": 37.58}} for c in codes})
    return db


def test_raw_gate_red_when_unflipped(data_db, monkeypatch):
    """母库有 10-28 参照而我们没重跑 ⇒ 逐日闸 unflipped ⇒ 检查项红。"""
    r = mv.check_raw_gate(data_db, ["000651"], TODAY, days=10)
    assert r["status"] == "red" and "未翻转" in r["detail"]


def test_raw_gate_green_when_all_checked(data_db):
    data_db.upsert_raw_bars(
        [Bar(symbol="000651", timestamp=datetime(2026, 10, d, 15, 0), open=37.5,
             high=37.7, low=37.4, close=37.58, volume=1000, amount=37580.0, adjust="raw")
         for d in (27, 28)], source="t46", checked_dates={"2026-10-27", "2026-10-28"})
    r = mv.check_raw_gate(data_db, ["000651"], "2026-10-28", days=10)
    assert r["status"] == "green", r["detail"]


def test_raw_gate_skip_without_symbols():
    assert mv.check_raw_gate(None, [], TODAY)["status"] == "skip"


# ---------- 汇总 ----------
def test_verify_all_flags_rollback_only_for_money_red(broker, data_db, monkeypatch):
    """非钱类红（影子留痕缺失等）只 exit 1；钱类红才 rollback_required。"""
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    add_fill(conn, commission=99.0)                 # 钱类红
    add_nav(conn)
    conn.close()
    res = mv.verify_all(broker=broker, data_db=data_db, symbols=["000651"],
                        today=TODAY, cfg=TradingConfig(), days=10)
    assert res["rollback_required"] is True
    assert "fees" in res["money_reds"]
    assert res["items"][0]["status"] == "red"       # 红的排在最前


def test_verify_all_no_rollback_for_non_money_red(broker, data_db):
    """钱类全绿、只有行情台账红（unflipped）⇒ 红但不触发回退标志。"""
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    add_nav(conn)                                  # nav 绿；无成交 ⇒ fees 只 warn
    conn.close()
    res = mv.verify_all(broker=broker, data_db=data_db, symbols=["000651"],
                        today=TODAY, cfg=TradingConfig(), days=10)
    reds = [i["id"] for i in res["items"] if i["status"] == "red"]
    assert "raw_gate" in reds, res
    assert res["money_reds"] == [] and res["rollback_required"] is False


def test_format_text_marks_rollback_line(broker, data_db):
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    add_fill(conn, commission=99.0)
    conn.close()
    res = mv.verify_all(broker=broker, data_db=data_db, symbols=["000651"],
                        today=TODAY, cfg=TradingConfig(), days=10)
    txt = mv.format_text(res)
    assert "立即回退" in txt and "❌" in txt and "[钱]" in txt
