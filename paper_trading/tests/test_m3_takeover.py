"""M3 接管（①②③）的钱类测试：决策时点门、逐笔归因、队列/供给报警、防双活互锁。

对应审计报告 `audit-20261030-m3-cutover.md` 的三条 P0：
- P0-2 每跳重放全天 240 根分钟 bar ⇒ 16:49 的决定成交在 09:31 的价；
- P0-3 意图按**标的**批量标 executed ⇒ 3 条 executed 对 1 笔成交，卖出机会被买单顺带吞掉；
- P1  静默故障（消费跳没跑 / 分钟线没采 / 队列积压）无人报警。

判定本体都做成了纯函数（`_SessionState.decide` / `queue.resolve_outcomes`），
所以这里不需要 rqalpha 就能把规则钉死。
"""
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.data.db_manager import DataDBManager
from paper_trading.integration.rq_engine import queue as Q
from paper_trading.integration.rq_engine.runner import _SessionState, _parse_dt
from paper_trading.models import TradingConfig
from paper_trading.utils import heartbeat as hb
from paper_trading.utils import m3_verify as mv

NOW = datetime(2026, 10, 30, 14, 30)


def intent(bid="b1", symbol="002192", action="buy", volume=100, decided_at=""):
    return {"biz_id": bid, "symbol": symbol, "action": action, "volume": volume,
            "side": action.upper(), "decided_at": decided_at or NOW.isoformat()}


# ==================================================== ① 决策时点门（P0-2）
@pytest.mark.parametrize("decided,now,expect", [
    ("2026-10-30T09:31:00", datetime(2026, 10, 30, 9, 31), "go"),      # 正好到点
    ("2026-10-30T14:29:59", datetime(2026, 10, 30, 14, 30), "go"),
    ("2026-10-30T16:49:24", datetime(2026, 10, 30, 9, 31), "wait"),    # 早盘重放不许执行盘后决定
    ("2026-10-30T16:49:24", datetime(2026, 10, 30, 15, 0), "wait"),    # 收盘前最后一根也不许
    ("", datetime(2026, 10, 30, 9, 31), "go"),                          # 没有决策时刻 ⇒ 旧行为
    ("不是日期", datetime(2026, 10, 30, 9, 31), "go"),
])
def test_decide_gates_on_decision_time(decided, now, expect):
    assert _SessionState.decide({"decided_at": decided}, now) == expect


def test_decide_accepts_real_timestamps():
    """现网 `rq_intents.created_at` 就是这种秒级 ISO 串，解析必须成功。"""
    assert _parse_dt("2026-10-30T16:49:24") == datetime(2026, 10, 30, 16, 49, 24)
    assert _parse_dt("2026-10-30 16:49:24") == datetime(2026, 10, 30, 16, 49, 24)
    assert _parse_dt("2026-10-30T16:49:24+08:00").hour == 16
    assert _parse_dt(None) is None


def test_decide_is_naive_comparable():
    """带 tz 与不带 tz 混比会抛 TypeError ⇒ 归一成 naive（与 context.now 同尺度）。"""
    got = _parse_dt("2026-10-30T16:49:24+08:00")
    assert got.tzinfo is None
    assert _SessionState.decide({"decided_at": "2026-10-30T16:49:24+08:00"},
                                datetime(2026, 10, 30, 9, 31)) == "wait"


# ==================================================== ② 逐笔归因（P0-3）
def test_resolve_wrong_side_or_volume_never_claims_first():
    """把"不对得上"的意图排在前面：只比标的（现网 2026-10-30 的写法）会让它先抢走成交。

    现网形状：`002192_BUY_100` 落账后，队列里同标的的 `SELL 100`、`BUY 200`
    因为"key 只比代码"被标 executed ⇒ 卖出机会被买单顺带吞掉、账实不符。
    """
    intents = [intent("b-sell", action="sell", volume=100),
               intent("b-vol", action="buy", volume=200),
               intent("b-real", action="buy", volume=100)]
    posted = [{"symbol": "002192", "side": "BUY", "volume": 100, "order_id": 99,
               "price": 44.94}]
    out = Q.resolve_outcomes(intents, posted, orders=[], submits=[])
    assert [u["biz_id"] for u in out["updates"]] == ["b-real"], out["updates"]
    assert out["stats"]["kept"] == 2
    assert out["unclaimed"] == []


def test_resolve_marks_only_the_matching_side_and_volume():
    """买 100 的成交绝不能替同标的的"卖 100"或"买 200"完成意图。"""
    intents = [intent("b1", action="buy", volume=100),
               intent("b2", action="sell", volume=100),
               intent("b3", action="buy", volume=200)]
    posted = [{"symbol": "002192", "side": "BUY", "volume": 100, "order_id": 77,
               "price": 44.94, "biz_id": "rq-fill:fp:0:002192.XSHE:100"}]
    out = Q.resolve_outcomes(intents, posted, orders=[], submits=[])
    assert [u["biz_id"] for u in out["updates"]] == ["b1"]
    assert out["updates"][0]["result"]["order_id"] == 77
    assert out["stats"] == {"executed": 1, "executed_idempotent": 0, "rejected": 0,
                            "kept": 2}


def test_resolve_one_fill_claims_one_intent_only():
    """两条同款意图 + 一笔成交 ⇒ 只有一条 executed，另一条留队列（不许多标）。"""
    intents = [intent("b1"), intent("b2")]
    posted = [{"symbol": "002192", "side": "BUY", "volume": 100, "order_id": 88}]
    out = Q.resolve_outcomes(intents, posted, orders=[], submits=[])
    assert [u["biz_id"] for u in out["updates"]] == ["b1"]
    assert out["stats"]["kept"] == 1


def test_resolve_rejects_submitted_but_unfilled_with_reason():
    """提交过而没落账成交 ⇒ rejected + 原因（收盘未成交也被框架 after_trading 拒）。"""
    intents = [intent("b1")]
    orders = [{"biz_id": "b1", "symbol": "002192.XSHE", "side": "BUY",
               "status": "REJECTED", "message": "Order Rejected: 可平仓量为 0", "filled": 0}]
    submits = [{"biz_id": "b1", "symbol": "002192.XSHE", "side": "BUY", "volume": 100}]
    out = Q.resolve_outcomes(intents, [], orders, submits)
    assert out["updates"][0]["status"] == "rejected"
    assert "可平仓量为 0" in out["updates"][0]["result"]["reason"]


def test_resolve_keeps_waiting_intents_queued():
    """决策时点未到 ⇒ 没提交、没成交 ⇒ 保持原状态，下一跳继续（不许被吞）。"""
    intents = [intent("b1", decided_at="2026-10-30T16:49:24")]
    out = Q.resolve_outcomes(intents, [], orders=[], submits=[], waiting={"b1"})
    assert out["updates"] == [] and out["stats"]["kept"] == 1
    assert out["waiting"] == ["b1"]


def test_resolve_idempotent_hit_leaves_queue():
    """账本当日已有同款成交（会话回报 SKIPPED_EXECUTED）⇒ 标 executed 并注明原因。"""
    intents = [intent("b1")]
    orders = [{"biz_id": "b1", "status": "SKIPPED_EXECUTED", "symbol": "002192.XSHE",
               "side": "BUY", "quantity": 100, "filled": 0}]
    out = Q.resolve_outcomes(intents, [], orders, submits=[])
    assert out["updates"][0]["status"] == "executed"
    assert "幂等命中" in out["updates"][0]["result"]["reason"]


def test_resolve_posting_failure_touches_nothing():
    """落账失败 ⇒ 一条都不许多标（半本账 + 蒸发意图是最坏组合）。"""
    intents = [intent("b1"), intent("b2")]
    posted = [{"symbol": "002192", "side": "BUY", "volume": 100, "order_id": 1}]
    out = Q.resolve_outcomes(intents, posted, orders=[], submits=[], posting_ok=False)
    assert out["updates"] == [] and out["stats"] == {"kept": 2}
    assert out["needs_attention"]


def test_resolve_reports_unclaimed_fill():
    """落了账却没有意图认领 ⇒ 显式暴露（对不上号必须看得见，不许默默吞在结果里）。"""
    out = Q.resolve_outcomes([], [{"symbol": "600000", "side": "BUY", "volume": 100}],
                             orders=[], submits=[])
    assert out["unclaimed"] == [{"symbol": "600000", "side": "BUY", "volume": 100}]


# ==================================================== 队列本体
@pytest.fixture()
def acct(tmp_path):
    return str(tmp_path / "paper_account.db")


def test_pending_carries_decided_at_and_expire_is_aware(acct):
    Q.enqueue(acct, "002192", "buy", 100, rule_ref="r1")
    with sqlite3.connect(acct) as c:                 # 造一条"昨天"的未决意图
        c.execute("UPDATE rq_intents SET created_at='2026-10-29T14:00:00'")
    Q.enqueue(acct, "600000", "buy", 100, rule_ref="r1")
    pend = Q.pending(acct)
    assert len(pend) == 2 and all(p.get("decided_at") for p in pend)
    expired = Q.expire_stale(acct, "2026-10-30")
    assert len(expired) == 1                          # 跨日的那条过期
    assert [p["symbol"] for p in Q.pending(acct)] == ["600000"]
    with sqlite3.connect(acct) as c:
        reason = c.execute("SELECT result FROM rq_intents WHERE status='expired'").fetchone()[0]
    assert "跨日" in reason


def test_enqueue_dedupes_only_unresolved(acct):
    """已 executed 的同款必须能再次入队（追加仓位），未决的同款不许重复入队。"""
    first = Q.enqueue(acct, "002192", "buy", 100)
    assert Q.enqueue(acct, "002192", "buy", 100) == "002192_BUY_100"   # 未决 ⇒ 去重
    assert len(Q.pending(acct)) == 1
    Q.mark(acct, first, "executed", {"order_id": 5})
    second = Q.enqueue(acct, "002192", "buy", 100)     # 同一秒再触发也不许被主键撞掉
    assert second != first
    assert [p["biz_id"] for p in Q.pending(acct)] == [second]


def test_backlog_reports_status_distribution(acct):
    Q.enqueue(acct, "002192", "buy", 100)
    b = Q.backlog(acct)
    assert b["exists"] is True and b["by_status"].get("queued") == 1
    assert b["oldest_pending"].startswith("2026") or b["oldest_pending"] is not None


@pytest.fixture()
def broker(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=100000.0))


# ==================================================== ③ 报警项
def test_intent_queue_red_when_executed_has_no_ledger_trace(broker):
    """P0-3 的形状：标了 executed 但 result 里没有 order_id ⇒ 必须红。"""
    with sqlite3.connect(str(broker.db_path)) as c:
        c.executescript(Q._SCHEMA)
        c.execute("INSERT INTO rq_intents (biz_id,symbol,side,volume,status,created_at,"
                  "result) VALUES ('b1','002192','BUY',100,'executed',?,'{}')",
                  (f"{date.today()}T14:30:00",))
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    r = mv.check_intent_queue(conn, date.today().isoformat(), now=NOW, executor="rqalpha")
    assert r["status"] == "red" and r["money"] is True
    conn.close()


def test_intent_queue_red_on_backlog(broker):
    with sqlite3.connect(str(broker.db_path)) as c:
        c.executescript(Q._SCHEMA)
        c.execute("INSERT INTO rq_intents (biz_id,symbol,side,volume,status,created_at)"
                  " VALUES ('b1','002192','BUY',100,'queued',?)",
                  ("2026-10-30T09:30:00",))
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    r = mv.check_intent_queue(conn, "2026-10-30", now=NOW, executor="rqalpha")
    assert r["status"] == "red" and "积压" in r["detail"]
    conn.close()


def test_intent_queue_green_and_skip_semantics(broker):
    """三种形状分开：未切换=不适用；切换了但表没建=红；表建了且队列空=绿。"""
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    assert mv.check_intent_queue(conn, "2026-10-30", executor="legacy")["status"] == "skip"
    assert mv.check_intent_queue(conn, "2026-10-30", executor="rqalpha")["status"] == "red"
    conn.close()
    with sqlite3.connect(str(broker.db_path)) as c:
        c.executescript(Q._SCHEMA)
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    r = mv.check_intent_queue(conn, "2026-10-30", now=NOW, executor="rqalpha")
    assert r["status"] == "green", r                       # 队列空 = 无事，不是红
    conn.close()


@pytest.fixture()
def data_db_with_minutes(tmp_path):
    from paper_trading.data.db_manager import DataDBManager

    db = DataDBManager(tmp_path / "data.db")
    with sqlite3.connect(str(db.db_path)) as c:
        c.execute("INSERT INTO minute_bars (symbol,ts,open,high,low,close,volume,amount,period)"
                  " VALUES ('002192','2026-10-30T14:25:00',44.9,44.9,44.9,44.9,100,4490,'1')")
    return db


def test_minute_feed_red_when_no_today_bars_in_session(data_db_with_minutes):
    r = mv.check_minute_feed(data_db_with_minutes, ["002192"], "2026-10-31",
                             now=datetime(2026, 10, 31, 10, 0), executor="rqalpha")
    assert r["status"] == "red" and "没有当天分钟线" in r["detail"]


def test_minute_feed_red_when_intraday_stale(data_db_with_minutes):
    """库里最新 14:25、此刻该有 14:50 ⇒ 滞后 25 分钟 > 阈值 20 ⇒ 红。"""
    r = mv.check_minute_feed(data_db_with_minutes, ["002192"], "2026-10-30",
                             now=datetime(2026, 10, 30, 14, 55), executor="rqalpha")
    assert r["status"] == "red" and "滞后" in r["detail"], r["detail"]


def test_minute_feed_lunch_break_is_not_stale(data_db_with_minutes):
    """11:30 收午盘后库里就停在 11:30 是**对的**——按墙钟判会把午休每小时弄红一次。

    现网 11-03 12:09 的真实误报：960 行 / 40 只、最新 11:30，红字写"陈旧 40 分钟"。
    """
    from paper_trading.data.db_manager import DataDBManager

    db = DataDBManager(str(data_db_with_minutes.db_path))
    with __import__("sqlite3").connect(str(db.db_path)) as c:
        c.execute("INSERT INTO minute_bars (symbol,ts,open,high,low,close,volume,amount,period)"
                  " VALUES ('000651','2026-11-03T11:30:00',10,10,10,10,100,1000,'5')")
        c.commit()
    r = mv.check_minute_feed(db, ["000651"], "2026-11-03",
                             now=datetime(2026, 11, 3, 12, 9), executor="rqalpha")
    assert r["status"] == "green", r["detail"]


def test_minute_feed_goes_red_again_after_lunch(tmp_path):
    """午后恢复交易却一整段没落库 ⇒ 报警必须重新响（午休豁免不是免死金牌）。"""
    from paper_trading.data.db_manager import DataDBManager

    db = DataDBManager(tmp_path / "d.db")
    with __import__("sqlite3").connect(str(db.db_path)) as c:
        c.execute("INSERT INTO minute_bars (symbol,ts,open,high,low,close,volume,amount,period)"
                  " VALUES ('000651','2026-11-03T11:30:00',10,10,10,10,100,1000,'5')")
        c.commit()
    # 13:11 时"该有"的是 13:05 那根（下午首根是 13:05，见 expected_slots 的约定）
    r = mv.check_minute_feed(db, ["000651"], "2026-11-03",
                             now=datetime(2026, 11, 3, 13, 11), executor="rqalpha")
    assert r["status"] == "red" and "滞后" in r["detail"], r["detail"]


def test_latest_expected_ts_walks_the_session_clock():
    from paper_trading.data.minute import latest_expected_ts

    assert latest_expected_ts("2026-11-03", "5", datetime(2026, 11, 3, 9, 20)) is None
    assert latest_expected_ts("2026-11-03", "5", datetime(2026, 11, 3, 9, 36)) == "2026-11-03T09:35"
    assert latest_expected_ts("2026-11-03", "5", datetime(2026, 11, 3, 12, 9)) == "2026-11-03T11:30"
    # 下午首根是 13:05（`expected_slots` 的既有约定），13:05 当时还没收完 ⇒ 仍是 11:30
    assert latest_expected_ts("2026-11-03", "5", datetime(2026, 11, 3, 13, 5)) == "2026-11-03T11:30"
    assert latest_expected_ts("2026-11-03", "5", datetime(2026, 11, 3, 13, 11)) == "2026-11-03T13:10"
    assert latest_expected_ts("2026-11-03", "5", datetime(2026, 11, 3, 15, 30)) == "2026-11-03T15:00"


def test_minute_feed_green_and_not_applicable(data_db_with_minutes):
    r = mv.check_minute_feed(data_db_with_minutes, ["002192"], "2026-10-30",
                             now=datetime(2026, 10, 30, 14, 35), executor="rqalpha")
    assert r["status"] == "green", r
    assert mv.check_minute_feed(data_db_with_minutes, ["002192"], "2026-10-30",
                                now=datetime(2026, 10, 30, 14, 35),
                                executor="legacy")["status"] == "skip"


def test_minute_feed_after_close_is_warn_not_red(data_db_with_minutes):
    """收盘后没有当天线只是"该查采集"，不是盘中停摆——不许钱类级别地响。"""
    r = mv.check_minute_feed(data_db_with_minutes, ["002192"], "2026-10-31",
                             now=datetime(2026, 10, 31, 17, 0), executor="rqalpha")
    assert r["status"] == "warn"


# ============================================ ②b 会话侧定局（假 rqalpha，现网实测形状）
class _Bar:
    def __init__(self, close):
        self.close = close


class _Ctx:
    def __init__(self, now):
        self.now = now


def _fake_rqalpha(monkeypatch):
    """把 `rqalpha.api.order_shares` 换成记数桩：本地没有 rqalpha 也能跑 handle_bar。"""
    import sys
    import types

    calls = []
    api = types.ModuleType("rqalpha.api")

    def order_shares(ob, vol):
        calls.append((ob, vol))
        return None                     # 返回 None ⇒ 走 NOT_CREATED 记录分支

    api.order_shares = order_shares
    root = types.ModuleType("rqalpha")
    root.api = api
    monkeypatch.setitem(sys.modules, "rqalpha", root)
    monkeypatch.setitem(sys.modules, "rqalpha.api", api)
    return calls


def test_handle_bar_sets_each_intent_once_and_tags_duplicates(monkeypatch):
    """日内续跑重放多根 bar：一条意图只定一次局，同跳同款第二条记 DUPLICATE。

    现网 2026-10-30 的实测形状——旧写法把"会话内去重"也记成 SKIPPED_EXECUTED，
    于是 3 条意图全部标 executed，而账本只有 1 笔成交。
    """
    calls = _fake_rqalpha(monkeypatch)
    plan = [intent("b1", decided_at="2026-10-30T14:30:00"),
            intent("b2", decided_at="2026-10-30T14:30:05"),        # 同款第二条
            intent("b3", symbol="000858", action="sell", decided_at="2026-10-30T14:30:00")]
    st = _SessionState(plan, executed_ids=set())
    bar_dict = {"002192.XSHE": _Bar(45.04), "000858.XSHE": _Bar(60.0)}
    for hh, mm in ((9, 31), (14, 29)):                   # 决策时点之前的两根 bar
        st.handle_bar(_Ctx(datetime(2026, 10, 30, hh, mm)), bar_dict)
    assert st.submits == [] and len(st.waiting) == 3    # 只记等待，一单未提
    st.handle_bar(_Ctx(datetime(2026, 10, 30, 14, 30)), bar_dict)
    st.handle_bar(_Ctx(datetime(2026, 10, 30, 14, 31)), bar_dict)   # 之后的 bar 继续重放
    assert [c[0] for c in calls] == ["002192.XSHE", "000858.XSHE"], calls
    statuses = {o["biz_id"]: o["status"] for o in st.orders}
    assert statuses["b2"] == "SKIPPED_DUPLICATE"          # 不是 SKIPPED_EXECUTED
    assert len([o for o in st.orders if o["biz_id"] == "b1"]) == 1   # 不重复定局
    assert len(st.waiting) == 3                           # 原始累积表仍留着早盘的"等"
    assert st.final_waiting() == []                       # 定局表：三条都已定局
    out = Q.resolve_outcomes(plan, [{"symbol": "002192", "side": "BUY", "volume": 100,
                                     "order_id": 1, "price": 45.04}],
                             st.orders, st.submits,
                             {w["biz_id"] for w in st.waiting})
    got = {u["biz_id"]: u["status"] for u in out["updates"]}
    assert got == {"b1": "executed", "b2": "rejected", "b3": "rejected"}, got
    assert out["stats"]["executed"] == 1                  # executed 笔数 == 成交笔数


def test_ledger_idempotent_key_still_marks_executed(monkeypatch):
    """账本当日真有同款成交（跨跳续跑）⇒ 这条意图确实不用再做，标 executed 合理。"""
    _fake_rqalpha(monkeypatch)
    plan = [intent("b1", decided_at="2026-10-30T14:30:00")]
    st = _SessionState(plan, executed_ids={"002192_BUY_100"})
    st.handle_bar(_Ctx(datetime(2026, 10, 30, 14, 35)),
                  {"002192.XSHE": _Bar(45.0)})
    assert st.orders[0]["status"] == "SKIPPED_EXECUTED"
    assert st.orders[0]["from"] == "ledger"
    out = Q.resolve_outcomes(plan, [], st.orders, st.submits, set())
    assert out["updates"][0]["status"] == "executed"
    assert out["stats"] == {"executed": 0, "executed_idempotent": 1,
                           "rejected": 0, "kept": 0}


def test_by_biz_keeps_real_status_over_later_skipped_record():
    """同一 biz 被记两条时，补记的 SKIPPED_* 不许盖掉真状态（洗白路径）。"""
    intents = [intent("b1")]
    orders = [{"biz_id": "b1", "status": "NOT_CREATED", "message": "可平仓量为 0"},
              {"biz_id": "b1", "status": "SKIPPED_EXECUTED", "from": "ledger"}]
    out = Q.resolve_outcomes(intents, [], orders, submits=[{"biz_id": "b1"}])
    assert out["updates"][0]["status"] == "rejected"
    assert "可平仓量为 0" in out["updates"][0]["result"]["reason"]


# ==================================================== ③b 分钟线日内采集（新线命脉）
def _bars(day: str, slots: list[str], px=44.9):
    return [{"ts": f"{day}T{s}", "open": px, "high": px, "low": px, "close": px,
             "volume": 100.0, "amount": px * 100} for s in slots]


def test_completed_slots_excludes_the_bar_still_running():
    from paper_trading.data.minute import completed_slots

    got = completed_slots("5", datetime(2026, 10, 30, 14, 37))
    assert got[-1] == "14:35"                  # 14:40 那根还在走 ⇒ 不算
    assert completed_slots("5", datetime(2026, 10, 30, 9, 20)) == []
    assert len(completed_slots("5", datetime(2026, 10, 30, 15, 30))) == 48


def test_ensure_today_minutes_writes_only_completed_bars(tmp_path):
    from paper_trading.data.minute import completed_slots, ensure_today_minutes

    db = DataDBManager(tmp_path / "data.db")
    now = datetime(2026, 10, 30, 14, 37)
    done = completed_slots("5", now)
    assert len(done) == 43                      # 09:35..14:35（上午 24 + 下午 19）

    def fake(sym, period, days, n):
        # 源比库里"多给一根还在走的 14:40"——落库必须把它筛掉
        return _bars("2026-10-30", done + ["14:40"])

    out = ensure_today_minutes(db, ["002192"], now=now, fetch=fake)
    assert out["written"] == 43
    have = {str(t)[:16] for t in db.minute_ts_on("002192", "2026-10-30", period="5")}
    assert "2026-10-30T14:40:00" not in have and "2026-10-30T14:40" not in have
    assert out["per_symbol"]["002192"]["missing"] == []
    assert out["errors"] == []


def test_ensure_today_minutes_skips_symbols_already_complete(tmp_path):
    """库里当日已齐 ⇒ 一个接口请求都不发（盘中每 5 分钟一跳，不许打爆行情源）。"""
    from paper_trading.data.minute import ensure_today_minutes

    db = DataDBManager(tmp_path / "data.db")
    from paper_trading.data.minute import completed_slots

    slots = completed_slots("5", datetime(2026, 10, 30, 14, 37))
    db.upsert_minute_bars("002192", _bars("2026-10-30", slots), period="5")
    calls: list[str] = []

    def fake(sym, period, days, now):
        calls.append(sym)
        return []

    out = ensure_today_minutes(db, ["002192"], now=datetime(2026, 10, 30, 14, 37),
                               fetch=fake)
    assert calls == [] and out["written"] == 0
    assert out["per_symbol"]["002192"]["missing"] == []


def test_ensure_today_minutes_reports_source_gaps_and_failures(tmp_path):
    """源没给那根 bar / 拉取抛错 ⇒ 如实进 missing 与 errors（纪律 4：不静默）。"""
    from paper_trading.data.minute import ensure_today_minutes

    db = DataDBManager(tmp_path / "data.db")

    def short(sym, period, days, now):
        return _bars("2026-10-30", ["09:35"])

    out = ensure_today_minutes(db, ["002192"], now=datetime(2026, 10, 30, 14, 37),
                               fetch=short)
    v = out["per_symbol"]["002192"]
    assert v["missing"] and "源无这些 bar" in v["error"]
    assert out["errors"] == ["002192"]

    def boom(sym, period, days, now):
        raise RuntimeError("限流")

    out2 = ensure_today_minutes(db, ["600000"], now=datetime(2026, 10, 30, 14, 37),
                                fetch=boom)
    assert "RuntimeError" in out2["per_symbol"]["600000"]["error"]
    assert out2["written"] == 0 and out2["errors"] == ["600000"]


# ==================================================== ③c AI 直接动作的防双活互锁
def _agent_trader(tmp_path):
    import paper_trading.cli as hb
    from paper_trading.agent import AgentConfig, AgentTrader

    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml")
    return AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0))


def _act(sym="600519", action="buy", volume=100, price=10.0):
    return [{"action": action, "symbol": sym, "volume": volume, "price": price,
             "price_source": "last_close", "reason": "接管验收",
             "basis": ["recent_closes"]}]


def _run(t, actions, **kw):
    return t._execute_actions(actions=actions, summary="", symbols=["600519"],
                              all_bars={}, latest_prices={"600519": 10.0},
                              cash=1000000.0, positions={}, nav_total=1000000.0,
                              dry_run=False, mode="live", now=NOW.isoformat(),
                              from_plan=True, **kw)


def test_agent_direct_action_enqueues_when_rqalpha(tmp_path, monkeypatch):
    """底座=rqalpha 时 AI 直接动作**不许**再走旧撮合，必须投意图队列（否则双活）。"""
    t = _agent_trader(tmp_path)
    monkeypatch.setattr(t, "_executor_of", lambda: "rqalpha")
    res = _run(t, _act())
    rec = res["decisions"][0]
    assert "已入意图队列" in rec["status"], rec
    assert rec.get("queued_rq") is True
    with sqlite3.connect(str(t.bridge.broker.db_path)) as c:
        rows = c.execute("SELECT biz_id, symbol, side, volume, status, rule_ref"
                         " FROM rq_intents").fetchall()
    assert len(rows) == 1 and rows[0][4] == "queued" and rows[0][1] == "600519"
    # 旧撮合器一笔都没动：账本里没有 orders/fills（这是"双活零成交"的硬证据）
    with sqlite3.connect(str(t.bridge.broker.db_path)) as c:
        n_orders = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert n_orders == 0


def test_agent_direct_action_refuses_when_executor_unreadable(tmp_path, monkeypatch):
    """底座读不出来 ⇒ 一条都不许下（两侧都可能是活的，拿不准就不动钱）。"""
    t = _agent_trader(tmp_path)
    monkeypatch.setattr(t, "_executor_of", lambda: "unknown(ValueError)")
    res = _run(t, _act())
    assert "执行底座无法判定" in res["decisions"][0]["status"]
    with sqlite3.connect(str(t.bridge.broker.db_path)) as c:
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        has_q = c.execute("SELECT 1 FROM sqlite_master WHERE name='rq_intents'"
                          ).fetchone()
        assert not has_q or c.execute(
            "SELECT COUNT(*) FROM rq_intents").fetchone()[0] == 0


def test_agent_executor_of_uses_same_config_reader(tmp_path, monkeypatch):
    """底座读法与 engine/rq tick 同源：engine.local.yaml 写 rqalpha 就必须读到 rqalpha。"""
    t = _agent_trader(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "engine.local.yaml").write_text(
        "engine:\n  executor: rqalpha\n", encoding="utf-8")
    assert t._executor_of() == "rqalpha"
    (tmp_path / "engine.local.yaml").write_text(
        "engine:\n  executor: 乱写\n", encoding="utf-8")
    assert t._executor_of().startswith("unknown(")     # 非法值不许被当成 legacy


def test_agent_direct_action_still_fills_on_legacy(tmp_path, monkeypatch):
    """没切换的部署行为不变（互锁不许把旧线一起闸死）。"""
    t = _agent_trader(tmp_path)
    monkeypatch.setattr(t, "_executor_of", lambda: "legacy")
    res = _run(t, _act())
    assert "已成交" in res["decisions"][0]["status"], res["decisions"][0]
    with sqlite3.connect(str(t.bridge.broker.db_path)) as c:
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_minute_heartbeat_required_only_after_collector_installed(tmp_path):
    """采集器"装过才追问"：没有 minute 记录 ⇒ 不切不假红；有记录但超时 ⇒ 必须响。"""
    path = tmp_path / "hb.json"
    fresh = (NOW - timedelta(minutes=1)).isoformat(timespec="seconds")
    old = (NOW - timedelta(minutes=40)).isoformat(timespec="seconds")
    path.write_text(json.dumps({"engine": {"ts": fresh, "ok": True},
                                "rq_tick": {"ts": fresh, "ok": True}}), encoding="utf-8")
    out = mv.check_heartbeat(path=path, now=NOW, executor="rqalpha")
    assert out["status"] == "green", out

    path.write_text(json.dumps({"engine": {"ts": fresh, "ok": True},
                                "rq_tick": {"ts": fresh, "ok": True},
                                "minute": {"ts": old, "ok": True}}), encoding="utf-8")
    out2 = mv.check_heartbeat(path=path, now=NOW, executor="rqalpha")
    assert out2["status"] == "red" and "minute" in out2["detail"]

    out3 = mv.check_heartbeat(path=path, now=NOW, executor="legacy")
    assert out3["status"] == "green", out3        # 没切换的部署不背这个红


# ==================================================== 心跳分层与防双活
def test_rq_tick_heartbeat_only_required_when_switched(tmp_path):
    import json

    hb_file = tmp_path / "heartbeat.json"
    hb_file.write_text(json.dumps({"engine": {"ts": NOW.isoformat(), "ok": True},
                                   "agent": {"ts": NOW.isoformat(), "ok": True}}),
                       encoding="utf-8")
    legacy = mv.check_heartbeat(path=hb_file, now=NOW + timedelta(minutes=1),
                                executor="legacy")
    assert legacy["status"] == "green"
    switched = mv.check_heartbeat(path=hb_file, now=NOW + timedelta(minutes=1),
                                  executor="rqalpha")
    assert switched["status"] == "red" and "rq_tick" in switched["detail"]


def test_rq_tick_due_window_excludes_night_and_weekend():
    assert hb.due("rq_tick", NOW) is True
    assert hb.due("rq_tick", datetime(2026, 10, 30, 20, 30)) is False
    assert hb.due("rq_tick", datetime(2026, 10, 31, 10, 0)) is False   # 周六


def test_rq_tick_threshold_not_in_defaults():
    """没切换的部署不该被 rq_tick 假红 ⇒ 它不进 DEFAULT_THRESHOLDS。"""
    assert "rq_tick" not in hb.DEFAULT_THRESHOLDS
    assert hb.RQ_TICK_THRESHOLD == 900


# ============================ ②c bundle 覆盖边界（T59 现网照出的第三条静默路径）
def test_mark_unavailable_settles_symbols_the_bundle_does_not_have(monkeypatch):
    """bundle 里没有的标的（北交所 920 段）必须当场定局，不许一直"等"到明天。

    现网 2026-11-03：池里 `920156` 而 bundle 的 28,063 只里 `.BJ` 为 0，
    `bar_dict[ob]` 抛异常被当成"这分钟没 bar"跳过 ⇒ IdleSpin 闸响过之后，
    意图仍以 queued 状态挂到次日被 `expire_stale` 清掉——机会丢了且原因不可见。
    """
    plan = [intent("b-bj", symbol="920156", decided_at="2026-11-03T11:20:00"),
            intent("b-ok", symbol="002192", decided_at="2026-11-03T11:20:00")]
    st = _SessionState(plan, executed_ids=set())
    settled = st.mark_unavailable({"002192.XSHE"})
    assert settled == ["b-bj"]
    rec = [o for o in st.orders if o["biz_id"] == "b-bj"][0]
    assert rec["status"] == "INSTRUMENT_UNAVAILABLE" and "920156.BJ" in rec["message"]
    assert not [o for o in st.orders if o["biz_id"] == "b-ok"]   # 在 bundle 里的不受影响
    out = Q.resolve_outcomes(plan, [], st.orders, st.submits, set(st.unavailable))
    got = {u["biz_id"]: (u["status"], u["result"].get("reason", "")) for u in out["updates"]}
    assert got["b-bj"][0] == "rejected" and "920156.BJ" in got["b-bj"][1]
    assert out["stats"]["kept"] == 1                            # b-ok 仍在正常路径上等 bar


def test_mark_unavailable_distinguishes_no_table_from_empty_table():
    """"问不到表"与"问到了、这些标的都不在表里"是两件事（现网重跑照出来的）。

    上一版把两者混成 `if not known: return []`，于是**整跳标的都不在 bundle**
    的情况（现网 920156 单独触发时正是这样）一条都不定局；而"问不到表"确实
    该什么都不做——不许靠猜测判死一条意图。
    """
    st = _SessionState([intent("b1", symbol="920156")], executed_ids=set())
    assert st.mark_unavailable(None) == []          # 问不到表 ⇒ 不判
    assert st.orders == []
    st2 = _SessionState([intent("b1", symbol="920156")], executed_ids=set())
    assert st2.mark_unavailable(set()) == ["b1"]    # 问到了且为空 ⇒ 全部定局
    assert st2.orders[0]["status"] == "INSTRUMENT_UNAVAILABLE"
