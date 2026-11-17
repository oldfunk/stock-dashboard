"""T85：daily-check 判别力补强（T81 P2 三项）+ Q8 取证产物（NAV 收盘口径）。

这里每条都对着一个**曾经报绿的真实现场**：
* 意图标 executed 但账本没有那笔成交（旧代码只数 filled，不比照）；
* 昨日未决意图没人过期处理（旧代码一律 green）；
* 框架比账本**多**仓（旧代码只查少）；
* 当日唯一一行 NAV 是 09:30 的盘中快照（旧代码只数行数 ⇒ 绿着的假账）。
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # import ci_gate_selfcheck

from paper_trading.broker.paper_broker import PaperBroker     # noqa: E402
from paper_trading.integration.rq_engine import queue as q     # noqa: E402
from paper_trading.models import TradingConfig                 # noqa: E402
from paper_trading.utils import daily_check as dc              # noqa: E402
from paper_trading.utils import m3_verify as mv                # noqa: E402

TODAY = "2026-11-13"
YDAY = "2026-11-12"


@pytest.fixture()
def db(tmp_path):
    br = PaperBroker(db_path=str(tmp_path / "acct.db"),
                     config=TradingConfig(initial_cash=50000.0))
    with br._connect() as c:                       # noqa: SLF001 - 造核对用的账本形状
        c.execute("DELETE FROM positions")
        c.execute("INSERT INTO positions (symbol, total_volume, available_volume,"
                  " avg_cost, last_update) VALUES ('002192',100,100,47.3,'x')")
    return str(br.db_path)


def fill(db, symbol="002192", direction=-1, volume=100, price=47.3, ts=None):
    stamp = ts or f"{TODAY}T09:31:05"
    with sqlite3.connect(db) as c:
        cur = c.execute(
            "INSERT INTO orders (symbol, direction, volume, order_type, status,"
            " filled_volume, filled_price, created_at, filled_at)"
            " VALUES (?,?,?,'LIMIT','filled',?,?,?,?)",
            (symbol, direction, volume, volume, price, stamp, stamp))
        oid = int(cur.lastrowid)
        c.execute("INSERT INTO fills (order_id, symbol, direction, volume, price,"
                  " commission, stamp_duty, transfer_fee, timestamp)"
                  " VALUES (?,?,?,?,?,5.0,0.0,0.0,?)",
                  (oid, symbol, direction, volume, price, stamp))
    return oid


def intent(db, symbol="002192", side="sell", volume=100, created=None,
           status="executed", result=None):
    biz = q.enqueue(db, symbol, side, volume)
    with q._conn(db) as c:                          # noqa: SLF001 - 只动测试造的库
        c.execute("UPDATE rq_intents SET created_at=?, status=?, result=? WHERE biz_id=?",
                  (created or f"{TODAY}T09:31:00", status,
                   json.dumps(result or {}, ensure_ascii=False), biz))
    return biz


# ------------------------------------------------- ① executed ↔ 账本一致性
def test_executed_with_matching_fill_is_green(db):
    oid = fill(db)
    intent(db, result={"order_id": oid, "price": 47.3})
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "green", r["detail"]
    assert "凭据" in r["detail"]


def test_executed_without_ledger_fill_is_money_red(db):
    """意图表说成交了、账本没有 ⇒ 假账，必须钱类红（旧代码这里报绿）。"""
    intent(db, result={"order_id": 999, "price": 47.3})
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "red" and r["money"] is True
    assert "999" in r["detail"]


def test_executed_with_no_evidence_at_all_is_money_red(db):
    intent(db, result={"fill": 47.3})               # 旧测试里那种"随手标 executed"
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "red" and r["money"] is True
    assert "无成交凭据" in r["detail"]


def test_executed_claiming_wrong_side_is_money_red(db):
    oid = fill(db, direction=1)                     # 账本是买，意图是卖
    intent(db, side="sell", result={"order_id": oid})
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "red" and "成交与意图不符" in r["detail"]


def test_executed_claiming_wrong_volume_is_money_red(db):
    oid = fill(db, volume=200)
    intent(db, volume=100, result={"order_id": oid})
    assert dc.check_intent_chain(db, TODAY)["status"] == "red"


def test_idempotent_hit_with_same_key_fill_is_green(db):
    fill(db)
    intent(db, result={"reason": "幂等命中：当日同款已成交"})
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "green", r["detail"]


def test_idempotent_hit_without_fill_is_money_red(db):
    intent(db, result={"reason": "幂等命中：当日同款已成交"})
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "red" and "没有同款成交" in r["detail"]


def test_unclaimed_fill_is_warn_not_red(db):
    """条件单/旧线成交没有意图认领：要吭一声，但不能一律当假账。"""
    fill(db)
    intent(db, status="rejected", result={"reason": "NOT_CREATED"})
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "warn" and r["money"] is False
    assert "无意图认领" in r["detail"]


def test_carried_intent_claims_today_fill(db):
    """昨天盘后决定、今天顺延成交：认领关系要跨日看，否则天天假 warn。"""
    oid = fill(db)
    intent(db, created=f"{YDAY}T15:09:20", result={"order_id": oid})
    r = dc.check_intent_chain(db, TODAY)
    assert r["status"] == "skip"                    # 当日没有新意图
    with sqlite3.connect(db) as c:
        c.execute("UPDATE rq_intents SET created_at=?", (f"{TODAY}T09:31:00",))
    assert dc.check_intent_chain(db, TODAY)["status"] == "green"


# ------------------------------------------------- ② 未顺延的过期未决意图
def test_stale_pending_from_yesterday_is_warn(db):
    intent(db, created=f"{YDAY}T09:31:00", status="queued")
    r = dc.check_carry_over(db, TODAY, executor="rqalpha")
    assert r["status"] == "warn", r["detail"]
    assert "过期处理" in r["detail"]


def test_stale_expired_from_yesterday_is_green(db):
    intent(db, created=f"{YDAY}T09:31:00", status="expired")
    r = dc.check_carry_over(db, TODAY, executor="rqalpha")
    assert r["status"] == "green", r["detail"]


def test_carry_over_still_warns_when_carried_not_settled(db):
    """顺延过来还挂着 ⇒ warn；日历答不出（不顺延）⇒ 落到"过期未决"也是 warn。"""
    intent(db, created=f"{YDAY}T15:09:20", status="queued")
    r = dc.check_carry_over(db, TODAY, executor="rqalpha",
                            now=datetime(2026, 11, 13, 15, 30))
    assert r["status"] == "warn", r["detail"]


# ------------------------------------------------- ③ 框架多仓也要拒开
def _ev(extra=None, framework=1, kept=(("002192.XSHE", 100),)):
    landed = {"checked": len(kept), "framework": framework}
    if extra is not None:
        landed["extra"] = extra
    return {"init_align": {"kept": [list(k) for k in kept], "dropped_silently": [],
                           "unsupported": []},
            "positions_landed": landed, "frozen_align": {"applied": []}}


def _state(tmp_path, ev):
    d = tmp_path / "state"
    d.mkdir(exist_ok=True)
    (d / f"evidence_{TODAY}.json").write_text(json.dumps(ev, ensure_ascii=False),
                                              encoding="utf-8")
    return str(d)


def test_init_align_red_on_extra_framework_position(tmp_path, db):
    ev = _ev(extra=[{"symbol": "600989.XSHG", "framework": 500, "ledger": 0}],
             framework=2)
    r = dc.check_init_align(_state(tmp_path, ev), db, TODAY)
    assert r["status"] == "red" and r["money"] is True
    assert "600989" in r["detail"]


def test_init_align_red_on_count_backstop_without_extra_field(tmp_path, db):
    """旧留痕没有逐只 extra：框架只数 > 账本只数也要响（不许因为格式旧就放过）。"""
    r = dc.check_init_align(_state(tmp_path, _ev(framework=3)), db, TODAY)
    assert r["status"] == "red" and "多仓" in r["detail"]


def test_init_align_green_when_extra_empty(tmp_path, db):
    r = dc.check_init_align(_state(tmp_path, _ev(extra=[])), db, TODAY)
    assert r["status"] == "green", r["detail"]


# ------------------------- ③b 同期比同期：不许拿期初快照去比"此刻"的账本
def test_init_align_green_after_positions_sold_out_today(tmp_path, db):
    """现网 11-12 的假红：期初 8 只、当天卖光 2 只 ⇒ 账本 6 只，不是"框架多仓"。

    彩排（候选树 + 生产库副本）实测这条把钱类红打到了 exit=3，而线是好的。
    """
    ev = _ev(framework=2, kept=(("002192.XSHE", 100), ("300390.XSHE", 200)))
    r = dc.check_init_align(_state(tmp_path, ev), db, TODAY)   # 账本只剩 002192
    assert r["status"] == "green", r["detail"]


def test_init_align_red_when_framework_short_at_init(tmp_path, db):
    """少仓用**同期**两个数判：期初实有 1 < 期初应有 2（11-05 现网形状）。"""
    ev = _ev(framework=1, kept=(("002192.XSHE", 100), ("300390.XSHE", 200)))
    r = dc.check_init_align(_state(tmp_path, ev), db, TODAY)
    assert r["status"] == "red" and r["money"] is True
    assert "1 只 < 账本应有 2 只" in r["detail"]


def test_init_align_green_for_position_bought_today(tmp_path, db):
    """当日买成交新建的仓不可能出现在期初留痕里 ⇒ 不是"账本有仓未进框架"。"""
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO positions (symbol, total_volume, available_volume,"
                  " avg_cost, last_update) VALUES ('600989',700,0,20.85,'x')")
    fill(db, symbol="600989", direction=1, volume=700, price=20.85)
    r = dc.check_init_align(_state(tmp_path, _ev()), db, TODAY)
    assert r["status"] == "green", r["detail"]


def test_init_align_red_for_old_position_never_fed(tmp_path, db):
    """豁免只给当日买成交：昨天就有、今天没买过的仓没进框架，照旧钱类红。"""
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO positions (symbol, total_volume, available_volume,"
                  " avg_cost, last_update) VALUES ('600989',700,700,20.85,'x')")
    r = dc.check_init_align(_state(tmp_path, _ev()), db, TODAY)
    assert r["status"] == "red" and r["money"] is True
    assert "600989" in r["detail"]


# ------------------------------------------------- ④ NAV 收盘口径（Q8）
def nav(c, ts, cash=32124.85, mv_=66626.0):
    c.execute("INSERT INTO nav_history (timestamp, cash, market_value, total_value,"
              " available_cash, pnl, pnl_pct) VALUES (?,?,?,?,?,?,?)",
              (ts, cash, mv_, cash + mv_, cash, 0.0, 0.0))
    c.commit()


def test_nav_red_when_sole_row_is_morning_snapshot(db):
    """现网 11-12 的形状：唯一一行 09:30，成交在 16:54 ⇒ 钱类红。"""
    fill(db, ts=f"{TODAY}T16:54:19")
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        nav(c, f"{TODAY}T09:30:42")
        r = mv.check_nav(c, TODAY, now=datetime(2026, 11, 13, 21, 30))
    assert r["status"] == "red" and r["money"] is True
    assert "不是收盘口径" in r["detail"] and "Q8" in r["on_red"]


def test_nav_warn_before_close_window(db):
    """15:30 的 cron 跑在收盘窗口之前：判 warn，不许天天假红。"""
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        nav(c, f"{TODAY}T09:30:42")
        r = mv.check_nav(c, TODAY, now=datetime(2026, 11, 13, 15, 30))
    assert r["status"] == "warn" and r["money"] is True
    assert "收盘窗口未过" in r["detail"]


def test_nav_green_when_row_is_close_caliber(db):
    fill(db, ts=f"{TODAY}T09:32:04")
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        nav(c, f"{TODAY}T16:31:17")
        r = mv.check_nav(c, TODAY, now=datetime(2026, 11, 13, 21, 30))
    assert r["status"] == "green", r["detail"]
    assert "收盘口径 16:31" in r["detail"]


def test_nav_red_when_close_row_predates_later_fill(db):
    """收盘行写了，但之后又补了成交（11-12 的 16:54 补跑）⇒ 那行仍然失真。"""
    fill(db, ts=f"{TODAY}T16:54:19")
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        nav(c, f"{TODAY}T16:31:17")
        r = mv.check_nav(c, TODAY, now=datetime(2026, 11, 13, 21, 30))
    assert r["status"] == "red" and "早于当日最后一笔成交" in r["detail"]


# ------------------------------------------------- ⑤ 执行侧：多仓拒开（需 rqalpha）
def test_mod_refuses_when_framework_has_extra_position():
    pytest.importorskip("rqalpha")
    from rqalpha.const import POSITION_DIRECTION

    from paper_trading.integration.rq_engine.mod_entry import PtPaperMod
    from paper_trading.integration.rq_engine.runner import InitPositionError

    mod = PtPaperMod()
    mod._init_align = {"unsupported": []}
    pos = SimpleNamespace(quantity=500, _non_closable=0)
    env = SimpleNamespace(portfolio=SimpleNamespace(stock_account=SimpleNamespace(
        _positions={"600989.XSHG": {POSITION_DIRECTION.LONG: pos}})))
    cfg = SimpleNamespace(init_positions={"002192.XSHE": 100})
    with pytest.raises(InitPositionError) as ei:
        mod._check_positions_landed(env, cfg)
    assert "多 1 只仓" in str(ei.value)
    assert mod._positions_landed["extra"][0]["symbol"] == "600989.XSHG"


def test_mod_accepts_equal_positions_and_records_empty_extra():
    pytest.importorskip("rqalpha")
    from rqalpha.const import POSITION_DIRECTION

    from paper_trading.integration.rq_engine.mod_entry import PtPaperMod

    mod = PtPaperMod()
    mod._init_align = {"unsupported": []}
    pos = SimpleNamespace(quantity=100, _non_closable=0)
    env = SimpleNamespace(portfolio=SimpleNamespace(stock_account=SimpleNamespace(
        _positions={"002192.XSHE": {POSITION_DIRECTION.LONG: pos}})))
    cfg = SimpleNamespace(init_positions={"002192.XSHE": 100})
    mod._check_positions_landed(env, cfg)
    assert mod._positions_landed == {"checked": 1, "framework": 1, "extra": []}


# ------------------------------------- ⑥ CI 门禁的合成基线不许随日历/时钟老化
@pytest.mark.parametrize("day", ["2026-11-12", "2026-11-14", "2026-12-31",
                                 "2027-06-01"])
def test_ci_gate_fixture_stays_fresh_on_any_run_date(tmp_path, day):
    """门禁基线必须在**任何**运行日都新鲜（11-14 是周六，2027 是日历缓存外）。

    实证：写死 11-02/03/04 的那版合成库，到 11-12 已"落后 6 个交易日"⇒
    raw_gate 钱类红 ⇒ M4-③ 的永久门禁天天非零（在 main 上同样复现）。
    """
    import ci_gate_selfcheck as gate
    from paper_trading.data.db_manager import DataDBManager

    fx = gate.build_fixture(tmp_path, day)
    data = DataDBManager(Path(fx["data_db"]))
    r = mv.check_raw_gate(data, [fx["symbol"]], day)
    assert r["status"] != "red", f"{day}: {r['detail']}"
    days = gate.recent_trading_days(day)
    assert len(days) == 3 and days[-1] <= day
    assert all(datetime.fromisoformat(d).weekday() < 5 for d in days)


@pytest.mark.parametrize("hhmm", ["09:10", "11:40", "14:59", "20:00"])
def test_ci_gate_fixture_feeds_minutes_at_any_clock(tmp_path, hhmm):
    """盘中跑门禁时，合成库必须有当日分钟线，否则 minute_feed 红（时钟敏感）。

    CI 的容器时钟是 UTC：09:05–15:05 UTC 推送就会落进"盘中"判据，
    而合成库原先一根分钟线都没有 ⇒ 同一份代码换个推送时刻就红。
    """
    import ci_gate_selfcheck as gate

    day = "2026-11-13"
    fx = gate.build_fixture(tmp_path, day)
    now = datetime.fromisoformat(f"{day}T{hhmm}:00")
    r = mv.check_minute_feed(fx["data_db"], [fx["symbol"]], day, now=now)
    assert r["status"] != "red", f"{hhmm}: {r['detail']}"