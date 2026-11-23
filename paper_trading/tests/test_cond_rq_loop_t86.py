"""T86 · 条件单在 rq 队列模式下的闭环（2026-11-17 生产缺陷回归）。

背景（11-17 生产照出）：条件单触发后走 `ConditionRunner._execute` 的 rq 分支，
先 `enqueue` 再 `orders.mark(d["id"], "queued_rq", ...)`——但 `STATUSES` 白名单
没有 `queued_rq` ⇒ `ValueError: 非法状态`（engine.log 250 次）。后果：
① 条件单停在 armed，引擎每跳重复入队（次日再买一次）；
② 同跳后续 armed 条件全被跳过（异常中断循环）；
③ 成交后也没人把条件单标 filled，面板常亮"待触发"，`cond_chain` 看不见。

本文件钉住：白名单、触发不再抛异常、不重复入队、成交/被拒后状态落定、
daily-check 新增的 `cond_loop` 与 `engine_crash` 两项判别力。
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.broker.paper_broker import PaperBroker  # noqa: E402
from paper_trading.engine.conditional import STATUSES, ConditionalOrders  # noqa: E402
from paper_trading.integration.rq_engine import queue as q  # noqa: E402
from paper_trading.models import TradingConfig  # noqa: E402
from paper_trading.utils import daily_check as dc  # noqa: E402

TODAY = datetime.now().date().isoformat()


def _bridge(tmp):
    import tempfile

    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                         secrets_path=str(Path(tmp) / "s.json"))
    b.llm_save("custom", "https://x/v1", "m", "k")
    return b


def _quotes(price=9.95):
    return {"000001": {"code": "000001", "price": price, "prev_close": 10.0,
                       "high": 10.0, "low": 9.9, "volume": 1e6,
                       "change_pct": -0.5}}


# ---------- 状态机白名单 ----------
def test_queued_rq_is_a_legal_status(tmp_path):
    co = ConditionalOrders(str(tmp_path / "a.db"))
    oid = co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
                     "cond_value": 10.0, "value": 20000})[2]
    assert "queued_rq" in STATUSES
    co.mark(oid, "queued_rq", 9.95, {"why": "交 rq 队列"})   # 旧实现抛 ValueError
    o = co.list_all(5)[0]
    assert o["status"] == "queued_rq" and o["filled_price"] == 9.95
    assert co.pending() == []           # 已交框架 ⇒ 不再每跳重复入队


def test_queued_rq_expires_with_the_order(tmp_path):
    co = ConditionalOrders(str(tmp_path / "a.db"))
    oid = co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
                     "cond_value": 10.0, "value": 20000,
                     "expiry": (datetime.now() + timedelta(hours=1)).isoformat()})[2]
    co.mark(oid, "queued_rq", 9.95)
    assert co.expire_due(now=datetime.now() + timedelta(hours=2)) == 1
    assert co.list_all(5)[0]["status"] == "expired"


# ---------- 触发不再崩、不重复入队 ----------
def test_rq_trigger_marks_queued_and_does_not_crash(tmp_path):
    from paper_trading.engine.runner import ConditionRunner
    from paper_trading.models import Bar

    b = _bridge(str(tmp_path))
    b.data_db.upsert_bars([Bar(symbol="000001",
                               timestamp=datetime.now() - timedelta(days=10000 + i),
                               open=10.0, high=10.2, low=9.8, close=10.0,
                               volume=100) for i in range(30)])
    db = str(b.broker.db_path)
    co = ConditionalOrders(db)
    oid = co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
                     "cond_value": 10.0, "value": 20000})[2]
    r = ConditionRunner(b, co, enabled=True, min_ticket_cny=1000,
                        executor="rq").tick(_quotes())
    assert "error" not in r and r["filled"], r
    assert co.list_all(5)[0]["status"] == "queued_rq"

    rows = sqlite3.connect(db).execute(
        "SELECT biz_id, rule_ref, status FROM rq_intents").fetchall()
    assert len(rows) == 1 and rows[0][1] == f"cond:{oid}" and rows[0][2] == "queued"

    # 第二跳：条件已交框架，不得再入队一次（旧实现每 5 分钟重复入队）
    r2 = ConditionRunner(b, co, enabled=True, min_ticket_cny=1000,
                         executor="rq").tick(_quotes())
    assert not r2.get("error") and r2["filled"] == []
    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM rq_intents").fetchone()[0]
    assert n == 1


# ---------- 成交/被拒后条件单落定 ----------
def test_apply_outcomes_fills_the_conditional(tmp_path):
    db = str(tmp_path / "acct.db")
    co = ConditionalOrders(db)
    oid = co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
                     "cond_value": 10.0, "value": 20000})[2]
    bid = q.enqueue(db, "000001", "buy", 2000, rule_ref=f"cond:{oid}")
    co.mark(oid, "queued_rq", 9.95)

    q.apply_outcomes(db, [{"biz_id": bid, "status": "executed",
                           "result": {"order_id": 33, "price": 9.94}}])
    o = co.list_all(5)[0]
    assert o["status"] == "filled"
    assert o["filled_at"] and o["filled_price"] == 9.94
    assert "rq_intent" in (o["detail"] or "")

    # 幂等命中那条（同款意图、无价格）不许把 filled_price 洗掉
    bid2 = q.enqueue(db, "000001", "buy", 2000, rule_ref=f"cond:{oid}")
    q.apply_outcomes(db, [{"biz_id": bid2, "status": "executed",
                           "result": {"reason": "幂等命中：当日同款已成交"}}])
    o2 = co.list_all(5)[0]
    assert o2["status"] == "filled" and o2["filled_price"] == 9.94


def test_apply_outcomes_rejects_the_conditional(tmp_path):
    db = str(tmp_path / "acct.db")
    co = ConditionalOrders(db)
    oid = co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
                     "cond_value": 10.0, "value": 20000})[2]
    bid = q.enqueue(db, "000001", "buy", 2000, rule_ref=f"cond:{oid}")
    co.mark(oid, "queued_rq", 9.95)
    q.apply_outcomes(db, [{"biz_id": bid, "status": "rejected",
                           "result": {"reason": "风控拒绝：现金不足"}}])
    o = co.list_all(5)[0]
    assert o["status"] == "rejected"
    assert "现金不足" in (o["detail"] or "")


def test_apply_outcomes_leaves_non_conditional_intents_alone(tmp_path):
    db = str(tmp_path / "acct.db")
    co = ConditionalOrders(db)
    oid = co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
                     "cond_value": 10.0, "value": 20000})[2]
    bid = q.enqueue(db, "000001", "buy", 100, rule_ref="ma-000001")
    q.apply_outcomes(db, [{"biz_id": bid, "status": "executed",
                           "result": {"order_id": 1, "price": 10.0}}])
    assert co.list_all(5)[0]["status"] == "armed"      # 规则单不碰条件单


# ---------- daily-check 判别力 ----------
def _acct(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=100000.0))


def _cond_intent(db, oid, status="executed"):
    """今日一条 cond 意图（走正式入队入口建表，再按判定结果推进状态）。"""
    bid = q.enqueue(db, "000001", "buy", 2000, rule_ref=f"cond:{oid}")
    q.mark(db, bid, status, {"order_id": 1, "price": 9.9})
    return bid


def test_cond_loop_red_when_executed_intent_but_order_still_armed(tmp_path):
    b = _acct(tmp_path)
    co = ConditionalOrders(str(b.db_path))
    oid = co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
                     "cond_value": 10.0, "value": 20000})[2]
    _cond_intent(b.db_path, oid, "executed")
    r = dc.check_cond_loop(b.db_path, TODAY)
    assert r["status"] == "red" and str(oid) in r["detail"]
    assert r["money"] is False            # 不是账目假账，但必须红出来
    assert "重复触发" in r["detail"]


def test_cond_loop_green_after_order_is_filled(tmp_path):
    b = _acct(tmp_path)
    co = ConditionalOrders(str(b.db_path))
    oid = co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
                     "cond_value": 10.0, "value": 20000})[2]
    co.mark(oid, "queued_rq", 9.95)
    bid = _cond_intent(b.db_path, oid, "queued")
    q.apply_outcomes(str(b.db_path), [{"biz_id": bid, "status": "executed",
                                       "result": {"order_id": 1, "price": 9.9}}])
    r = dc.check_cond_loop(b.db_path, TODAY)
    assert r["status"] == "green", r["detail"]


def test_cond_loop_skips_without_cond_intents(tmp_path):
    b = _acct(tmp_path)
    ConditionalOrders(str(b.db_path))
    q.enqueue(str(b.db_path), "000001", "buy", 100, rule_ref="ma-000001")
    r = dc.check_cond_loop(b.db_path, TODAY)
    assert r["status"] == "green" and "无条件触发" in r["detail"]
    assert dc.check_cond_loop(b.db_path, TODAY, executor="legacy")["status"] == "skip"


def test_engine_crash_check_warns_on_todays_crash(tmp_path):
    b = _acct(tmp_path)
    log = Path(str(b.db_path)).parent / "engine.log"
    log.write_text(f"[{TODAY} 10:41:02] [ERROR] [x] engine/conds crashed: "
                   f"ValueError: 非法状态：queued_rq\n"
                   f"[2026-01-01 09:00:00] [ERROR] engine/conds crashed: old\n",
                   encoding="utf-8")
    r = dc.check_engine_crashes(b.db_path, TODAY)
    assert r["status"] == "warn" and "crashed" in r["detail"]
    assert "非法状态" in r["detail"]


def test_engine_crash_check_green_without_crash(tmp_path):
    b = _acct(tmp_path)
    log = Path(str(b.db_path)).parent / "engine.log"
    log.write_text(f"[{TODAY} 10:41:02] [INFO] engine ok\n", encoding="utf-8")
    assert dc.check_engine_crashes(b.db_path, TODAY)["status"] == "green"


def test_engine_crash_check_ignores_old_history(tmp_path):
    b = _acct(tmp_path)
    log = Path(str(b.db_path)).parent / "engine.log"
    log.write_text("[2026-01-01 09:00:00] [ERROR] engine/conds crashed: old\n",
                   encoding="utf-8")
    assert dc.check_engine_crashes(b.db_path, TODAY)["status"] == "green"


def test_cli_tick_writeback_actually_goes_through_apply_outcomes():
    """钉住**生产接线本身**：tick 的写回只许走 `_q.apply_outcomes`，不许退回 `_q.mark`。

    2026-11-19 复核发现的缺口：muse 分支那两条 `tick_outcome_sequence_*` 是把
    cli.py 的调用顺序**抄进测试**（测试自己调 apply_outcomes），所以把 cli.py
    换回旧的 `_q.mark(...)` 版本，它们仍然全绿——测的是"这个顺序是对的"，
    不是"生产走的就是这个顺序"。这条直接读 cli.py 源码断言接线存在。
    """
    import paper_trading.cli as _cli

    text = Path(_cli.__file__).read_text(encoding="utf-8")
    # cli.py 里 `_q.` 只出现在 tick 写回这一处：有 apply_outcomes 就不许再有 mark。
    assert '_q.apply_outcomes(acct_db, verdict["updates"])' in text, \
        "tick 写回没走 queue.apply_outcomes ⇒ 条件单成交后永停 queued_rq"
    assert "_q.mark(" not in text, \
        "tick 写回退回 mark ⇒ 条件单永停 queued_rq（T86 事故）"


def test_tick_stall_warns_on_init_position_error_in_today_log(tmp_path):
    """11-18 的形状：每跳 0 退出、引擎日志无 `crashed`，但 tick 日志刷
    `InitPositionError` 且全天 0 成交——必须变成收盘报告里的黄，不许静默。"""
    b = _acct(tmp_path)
    log = Path(str(b.db_path)).parent / "rq_tick.log"
    log.write_text(
        f"[{TODAY} 10:07:04.253216] DEBUG: system_log: restore portfolio\n"
        f"[{TODAY} 10:07:04.269000] ERROR: user_system_log: 会话失败\n"
        "Traceback (most recent call last):\n"
        "  File \"runner.py\", line 1, in run_session\n"
        "paper_trading.integration.rq_engine.runner.InitPositionError: "
        "框架账户比账本多 1 只仓：[{'symbol': '000792.XSHE', "
        "'framework': 100, 'ledger': 0}]\n",
        encoding="utf-8")
    it = dc.check_tick_stall(b.db_path, TODAY)
    assert it["status"] == "warn", it
    assert "InitPositionError" in it["detail"]
    assert "000792" in it["detail"]


def test_tick_stall_warns_on_idle_spin_lines(tmp_path):
    b = _acct(tmp_path)
    log = Path(str(b.db_path)).parent / "rq_tick.log"
    log.write_text(f"[{TODAY} 10:12:05.000000] DEBUG: tick\n"
                   "        \"IdleSpin: handle_bar 一次都没执行（窗口/事件源/数据没接上）\",\n",
                   encoding="utf-8")
    assert dc.check_tick_stall(b.db_path, TODAY)["status"] == "warn"


def test_tick_stall_ignores_previous_day(tmp_path):
    """只扫当天：历史事故不该让之后每个交易日都黄（engine_crash 同款纪律）。"""
    b = _acct(tmp_path)
    log = Path(str(b.db_path)).parent / "rq_tick.log"
    log.write_text("[2026-01-01 10:07:04.000000] ERROR: crash\n"
                   "paper_trading.integration.rq_engine.runner.InitPositionError: old\n"
                   f"[{TODAY} 09:32:05.000000] DEBUG: 正常开会话\n",
                   encoding="utf-8")
    it = dc.check_tick_stall(b.db_path, TODAY)
    assert it["status"] == "green", it


def test_tick_stall_green_without_log_or_hits(tmp_path):
    b = _acct(tmp_path)
    assert dc.check_tick_stall(b.db_path, TODAY)["status"] == "skip"
    log = Path(str(b.db_path)).parent / "rq_tick.log"
    log.write_text(f"[{TODAY} 09:32:05.000000] DEBUG: 正常开会话\n", encoding="utf-8")
    assert dc.check_tick_stall(b.db_path, TODAY)["status"] == "green"
