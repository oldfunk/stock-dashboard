"""T80：每日核对（`m3 daily-check`）的判别力测试。

核对工具最容易犯的错是"永远绿"——这里每条都同时测绿与红，
红了必须能指到东西（哪只仓没对齐/哪条顺延没落地）。
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_trading.broker.paper_broker import PaperBroker     # noqa: E402
from paper_trading.integration.rq_engine import queue as q     # noqa: E402
from paper_trading.models import TradingConfig                 # noqa: E402
from paper_trading.utils import daily_check as dc              # noqa: E402

TODAY = "2026-11-06"


@pytest.fixture()
def acct(tmp_path):
    path = tmp_path / "acct.db"
    br = PaperBroker(db_path=str(path), config=TradingConfig(initial_cash=50000.0))
    with br._connect() as conn:                    # noqa: SLF001 - 造核对用的账本形状
        conn.execute("DELETE FROM positions")
        conn.execute("INSERT INTO positions (symbol, total_volume, available_volume,"
                     " avg_cost, last_update) VALUES ('002192',100,100,47.3,'x')")
        conn.execute("INSERT INTO positions (symbol, total_volume, available_volume,"
                     " avg_cost, last_update) VALUES ('300390',200,0,37.4,'x')")
    return str(path)


def _state(tmp_path, evidence=None, name="evidence_2026-11-06.json"):
    d = tmp_path / "state"
    d.mkdir(exist_ok=True)
    if evidence is not None:
        (d / name).write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    return str(d)


def _set_created(db, ts: str) -> None:
    """把刚入队的意图搬到指定时刻（核对项按 created_at 的日期分「当日/昨日」）。"""
    with q._conn(db) as c:                          # noqa: SLF001 - 只动测试造的库
        c.execute("UPDATE rq_intents SET created_at=?", (ts,))


def _fill(db, symbol="002192", direction=-1, volume=100, price=47.3, ts=None):
    """账本当日一笔 filled 成交——`executed` 的凭据（T85 一致性断言用）。"""
    stamp = ts or f"{TODAY}T09:31:05"
    with sqlite3.connect(db) as c:
        cur = c.execute(
            "INSERT INTO orders (symbol, direction, volume, order_type, status,"
            " filled_volume, filled_price, created_at, filled_at)"
            " VALUES (?,?,?,'LIMIT','filled',?,?,?,?)",
            (symbol, direction, volume, volume, price, stamp, stamp))
        return int(cur.lastrowid)


def _ev(kept=(("002192.XSHE", 100), ("300390.XSHE", 200)), dropped=(), uns=()):
    return {"init_align": {"kept": [list(k) for k in kept],
                           "dropped_silently": list(dropped),
                           "unsupported": list(uns)},
            "positions_landed": {"checked": len(kept), "framework": len(kept)},
            "frozen_align": {"applied": [{"symbol": "300390.XSHE", "to": 200}]}}


# ------------------------------------------------------------------ ① init 对齐
def test_init_align_green_covers_ledger(tmp_path, acct):
    r = dc.check_init_align(_state(tmp_path, _ev()), acct, TODAY)
    assert r["status"] == "green", r["detail"]
    assert "框架持仓 2 只/账本 2 只" in r["detail"]


def test_init_align_red_when_session_left_no_trail(tmp_path, acct):
    """会话该跑却没留痕 ⇒ 红（对齐无从证明），不是"没数据就算了"。"""
    r = dc.check_init_align(_state(tmp_path), acct, TODAY)
    assert r["status"] == "red" and "无会话留痕" in r["detail"]


def test_init_align_skip_when_no_work_expected(tmp_path, acct):
    """队列本来就空 ⇒ 没留痕是正常的，判 skip 不判红（报警不许失真）。"""
    r = dc.check_init_align(_state(tmp_path), acct, TODAY, work_expected=False)
    assert r["status"] == "skip"


def test_init_align_red_when_framework_swallowed_position(tmp_path, acct):
    r = dc.check_init_align(_state(tmp_path, _ev(dropped=[{"symbol": "002192.XSHE",
                                                           "expected": 100}])),
                           acct, TODAY)
    assert r["status"] == "red" and "吞掉" in r["detail"]


def test_init_align_red_when_ledger_position_absent_from_kept(tmp_path, acct):
    """账本有仓、`kept` 里没有 ⇒ 红（11-05 那本空仓账户的日常哨兵）。"""
    r = dc.check_init_align(_state(tmp_path, _ev(kept=(("002192.XSHE", 100),))),
                            acct, TODAY)
    assert r["status"] == "red" and "300390" in r["detail"]


def test_init_align_ignores_unsupported_instruments(tmp_path, acct):
    """框架没这合约（北交所）不算缺仓，但要在 detail 里点名。"""
    with sqlite3.connect(acct) as c:
        c.execute("INSERT INTO positions (symbol, total_volume, available_volume,"
                  " avg_cost, last_update) VALUES ('920156',100,100,10,'x')")
    ev = _ev(kept=(("002192.XSHE", 100), ("300390.XSHE", 200)),
             uns=[{"symbol": "920156.BJ", "volume": 100}])
    r = dc.check_init_align(_state(tmp_path, ev), acct, TODAY)
    assert r["status"] == "green", r["detail"]
    assert "920156.BJ" in r["detail"]


# ------------------------------------------------------------------ ② 顺延落地
def test_carry_over_warns_when_carried_intent_never_settled(tmp_path):
    db = str(tmp_path / "acct.db")
    q.enqueue(db, "002192", "sell", 100, rule_ref="t71")
    with q._conn(db) as c:                          # noqa: SLF001 - 造昨日盘后的意图
        c.execute("UPDATE rq_intents SET created_at='2026-11-05T15:09:20'")
    r = dc.check_carry_over(db, TODAY, executor="rqalpha")
    assert r["status"] == "warn" and "未定局" in r["detail"]


def test_carry_over_green_after_carried_intent_executed(tmp_path):
    db = str(tmp_path / "acct.db")
    q.enqueue(db, "002192", "sell", 100, rule_ref="t71")
    biz = q.pending(db)[0]["biz_id"]
    with q._conn(db) as c:                          # noqa: SLF001
        c.execute("UPDATE rq_intents SET created_at='2026-11-05T15:09:20'")
    q.mark(db, biz, "executed", {"fill": 47.3})
    r = dc.check_carry_over(db, TODAY, executor="rqalpha")
    assert r["status"] == "green" and "顺延 1 条" in r["detail"]


def test_carry_over_skips_on_legacy_executor(tmp_path):
    db = str(tmp_path / "acct.db")
    q.enqueue(db, "002192", "sell", 100)
    assert dc.check_carry_over(db, TODAY, executor="legacy")["status"] == "skip"


# ------------------------------------------------------------------ ③ 当日意图全链
def test_intent_chain_flags_open_intents_same_day(tmp_path):
    db = str(tmp_path / "acct.db")
    q.enqueue(db, "002192", "sell", 100, rule_ref="r")
    _set_created(db, f"{TODAY}T15:09:20")
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "warn"                       # 当日入队到收盘仍未定局


def test_intent_chain_green_when_all_settled(tmp_path, acct):
    db = acct
    q.enqueue(db, "002192", "sell", 100, rule_ref="r")
    biz = q.pending(db)[0]["biz_id"]
    with q._conn(db) as c:                             # noqa: SLF001 - 核对日用今天
        c.execute("UPDATE rq_intents SET created_at=?", (f"{TODAY}T09:31:00",))
    oid = _fill(db)
    q.mark(db, biz, "executed", {"order_id": oid, "price": 47.3})
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "green" and "executed" in r["detail"]


def test_intent_chain_skip_when_no_new_intents(tmp_path):
    db = str(tmp_path / "acct.db")
    q.enqueue(db, "002192", "sell", 100, rule_ref="r")
    with q._conn(db) as c:                             # noqa: SLF001
        c.execute("UPDATE rq_intents SET created_at='2026-11-01T09:31:00'")
    assert dc.check_intent_chain(db, TODAY)["status"] == "skip"


# ------------------------------------------------------------------ 汇总
def test_daily_check_composes_three_rq_items(tmp_path, acct):
    """没 broker 时只跑 rq 侧三项（CI 里可用），红项排在最前。"""
    out = dc.daily_check(data_db=None, account_db=acct, symbols=["002192"],
                         today=TODAY, cfg=None, state_dir=_state(tmp_path, _ev()),
                         broker=None, work_expected=True, executor="rqalpha")
    ids = {i["id"] for i in out["items"]}
    assert {"init_align", "carry_over", "intent_chain"} <= ids
    assert out["red"] == sum(1 for i in out["items"] if i["status"] == "red")
    assert out["ok"] is (out["red"] == 0)


def test_daily_check_exit_semantics_match_verify(tmp_path, acct):
    """钱类红 ⇒ rollback_required（与 `m3 verify` 的 3 码同一把尺子）。"""
    bad = _ev(dropped=[{"symbol": "002192.XSHE", "expected": 100}])
    out = dc.daily_check(data_db=None, account_db=acct, symbols=[], today=TODAY,
                         cfg=None, state_dir=_state(tmp_path, bad),
                         broker=None, work_expected=True, executor="rqalpha")
    assert out["red"] >= 1 and out["ok"] is False
