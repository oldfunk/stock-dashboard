"""T62：账本重置后的状态清理工具（`cli ledger reset-state` + 清单单一真相源）。

这里锁的是 2026-11-02 周一那次事故的形状：账本重开了，**库外的运行态没跟着开**，
旧账的峰值 156,005.23 拿量新账的 100,189.96 ⇒ 回撤 35.7% ⇒ 全天零成交，
第二天却被人读成"迁移失败"。所以每条测试都问同一件事：
* 清之前有没有把现场抄进 `op_log[ledger:reset-state]`（无留痕的清理=毁证）；
* 钱类表是不是**一根毛都没动**（越界就回滚，不是报错继续）；
* 该报的（逾期 T+1、意图/账本口径冲突、清单没见过的新键）不许被当成"清干净了"；
* 重复跑不写第二份留痕、不刷第二屏。

日期一律钉死（T53 的教训：跑在周末/月末就红的测试等于没有）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from paper_trading import ledger_state as ls
from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.engine.conditional import ConditionalOrders
from paper_trading.engine.state import EngineState
from paper_trading.integration.rq_engine import queue as Q
from paper_trading.models import TradingConfig
from paper_trading.risk.risk_manager import RiskManager, RiskState

TODAY = "2026-11-03"
NOW = datetime(2026, 11, 3, 10, 0, 0)
OLD_PEAK = 156_005.2264       # 周一现网那个孤儿峰值
NEW_NAV = 100_189.9591        # 重开后的真实净值


@pytest.fixture()
def broker(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "paper_account.db"),
                       config=TradingConfig(initial_cash=NEW_NAV))


def kv(db, table, key, value):
    """用真实持久层建表再写键——绕过构造函数去插一张不存在的表不是现场。"""
    (RiskState if table == "risk_state" else EngineState)(db)
    with sqlite3.connect(str(db)) as c:
        c.execute(f"INSERT INTO {table} (key, value, updated_at) VALUES (?, ?, ?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value), NOW.isoformat()))
        c.commit()


def rows(db, sql, args=()):
    with sqlite3.connect(str(db)) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def dirty_account(broker, tmp_path):
    """把周一那种"账本新、运行态旧"的现场摆出来（现网读到的真实形状）。"""
    db = broker.db_path
    RiskState(db).set("peak_total_value", f"{OLD_PEAK:.4f}")
    RiskState(db).set(f"daily_baseline:{TODAY}", f"{OLD_PEAK:.4f}")
    RiskState(db).set("daily_baseline:2026-11-02", f"{OLD_PEAK:.4f}")
    st = EngineState(db)
    st.set(f"fired:{TODAY}", ["trail-002192"])
    st.set(f"budget:{TODAY}", {"orders": 3, "turnover": 10610.0})
    st.set("cooldown:002192", "2026-11-02T09:30:05")
    st.set(f"trigger-once:{TODAY}:rule:trail-000792:000792:sell", NOW.isoformat())
    st.set("event:last", NOW.isoformat())
    st.set(f"event:budget:{TODAY}", 6)
    st.set("event:sym:000792", NOW.isoformat(timespec="seconds"))
    Q.enqueue(db, "002192", "buy", 100, rule_ref="rule:t62")
    cond = ConditionalOrders(db)
    cond.create({"symbol": "600036", "side": "buy", "cond_type": "price_below",
                 "cond_value": 40.5, "value": 20000.0, "expiry": "2026-11-05 23:59"},
                now=NOW)
    with sqlite3.connect(str(db)) as c:      # 逾期未解冻的 T+1（该报，不该被清）
        c.execute("INSERT INTO t1_freeze (symbol, volume, freeze_date,"
                  " unfreeze_date, is_unfrozen) VALUES ('002192', 100,"
                  " '2026-10-30', '2026-11-02', 0)")
        c.commit()
    d = tmp_path / "var" / "rq_state" / TODAY
    d.mkdir(parents=True, exist_ok=True)
    (d / "portfolio.state").write_text("旧账的持仓快照", encoding="utf-8")
    return db


# ==================================================== 熔断态：旧尺子不许量新账

def test_orphan_peak_halts_the_new_ledger_until_it_is_cleared(broker):
    """周一本体：先证明孤儿峰值真的把新账关在门外，再证明清完能开门。"""
    db = broker.db_path
    RiskState(db).set("peak_total_value", f"{OLD_PEAK:.4f}")
    rm = RiskManager(db_path=db, max_drawdown_pct=0.20)
    assert rm.check_drawdown(NEW_NAV)[0] is True          # 旧账的尺子：熔断

    out = ls.apply_reset(db, broker, reason="T62 测试：重置后清运行态", today=TODAY,
                         now=NOW, state_root=str(Path(db).parent / "none"))
    assert out["cleared_by_target"]["risk_peak"] == 1

    fresh = RiskManager(db_path=db, max_drawdown_pct=0.20)   # 重开进程读落库峰值
    assert fresh._peak_value == 0.0                          # noqa: SLF001
    assert fresh.check_drawdown(NEW_NAV)[0] is False         # 开门


def test_orphan_daily_baseline_halts_until_cleared(broker):
    """日亏基线是第二个孤儿尺子：156,005 基线量 100,190 ⇒ 天天判 35% 亏损。"""
    db = broker.db_path
    RiskState(db).set(f"daily_baseline:{TODAY}", f"{OLD_PEAK:.4f}")
    rm = RiskManager(db_path=db, daily_loss_halt_pct=0.05)
    halted, loss = rm.check_daily_loss(NEW_NAV, day=TODAY)
    assert halted is True and loss > 0.35

    ls.apply_reset(db, broker, reason="T62 测试", today=TODAY, now=NOW,
                   state_root=str(Path(db).parent / "none"))
    fresh = RiskManager(db_path=db, daily_loss_halt_pct=0.05)
    halted, loss = fresh.check_daily_loss(NEW_NAV, day=TODAY)
    assert (halted, loss) == (False, 0.0)      # 基线按新账重新锚定


def test_clear_writes_before_values_into_the_op_log_trail(broker):
    """删掉的值不可再生：留痕里没有 before，等于毁证（切换日那次改判吃过教训）。"""
    db = broker.db_path
    RiskState(db).set("peak_total_value", f"{OLD_PEAK:.4f}")
    ls.apply_reset(db, broker, reason="周一孤儿峰值", today=TODAY, now=NOW,
                   state_root=str(Path(db).parent / "none"))
    trail = rows(db, "SELECT * FROM op_log WHERE action=?", (ls.ACTION,))
    assert len(trail) == 1
    result = json.loads(trail[0]["result"])
    assert result["reason"] == "周一孤儿峰值"
    before = result["cleared"]["risk_peak"]["before"]
    assert before[0]["key"] == "peak_total_value"
    assert float(before[0]["value"]) == pytest.approx(OLD_PEAK)


def test_reset_state_requires_a_reason(broker):
    with pytest.raises(ValueError, match="reason"):
        ls.apply_reset(broker.db_path, broker, reason="  ", today=TODAY, now=NOW)


# ==================================================== 幽灵：旧决定不许动新账

def test_pending_intents_are_rejected_with_a_reason_not_deleted(broker):
    """意图行留着、状态定局：删掉就没法解释那天为什么没成交。"""
    db = broker.db_path
    bid = Q.enqueue(db, "002192", "buy", 100, rule_ref="rule:t62")
    assert len(Q.pending(db)) == 1

    ls.apply_reset(db, broker, reason="账本重开", today=TODAY, now=NOW,
                   state_root=str(Path(db).parent / "none"))
    assert Q.pending(db) == []
    row = rows(db, "SELECT status, result FROM rq_intents WHERE biz_id=?", (bid,))[0]
    assert row["status"] == "rejected"
    assert "账本重开" in json.loads(row["result"])["reason"]


def test_armed_conditional_orders_are_cancelled(broker):
    db = broker.db_path
    ConditionalOrders(db).create({"symbol": "600036", "side": "buy",
                                  "cond_type": "price_below", "cond_value": 40.5,
                                  "value": 20000.0, "expiry": "2026-11-05 23:59"},
                                 now=NOW)
    out = ls.apply_reset(db, broker, reason="账本重开", today=TODAY, now=NOW,
                         state_root=str(Path(db).parent / "none"))
    assert out["cleared_by_target"]["cond_armed"] == 1
    r = rows(db, "SELECT status, detail FROM conditional_orders")[0]
    assert r["status"] == "cancelled" and "账本重开" in r["detail"]
    # 流水没被抹：条件单历史是审计材料，只有状态推进
    assert rows(db, "SELECT COUNT(*) n FROM conditional_orders")[0]["n"] == 1


def test_session_state_dir_is_moved_aside_not_deleted(broker, tmp_path):
    """目录里是旧账的 portfolio 快照：可恢复性优先，改名而不是 rm。"""
    db = broker.db_path
    root = tmp_path / "var" / "rq_state"
    day = root / TODAY
    day.mkdir(parents=True)
    (day / "portfolio.state").write_text("旧账", encoding="utf-8")

    out = ls.apply_reset(db, broker, reason="账本重开", today=TODAY, now=NOW,
                         state_root=str(root))
    assert not day.exists()
    moved = list(root.glob(f"{TODAY}.pre-reset-*"))
    assert len(moved) == 1
    assert (moved[0] / "portfolio.state").read_text(encoding="utf-8") == "旧账"
    assert out["state_dirs_moved"] == [[str(day), str(moved[0])]]


def test_resumed_session_cannot_come_back_into_the_new_ledger(broker, tmp_path):
    """清完目录，会话闸就没有旧状态可续 ⇒ `rq tick` 只能新开会话接新账。"""
    from datetime import date

    from paper_trading.integration.rq_engine.provider import SessionGate

    db = broker.db_path
    root = tmp_path / "var" / "rq_state"
    day = root / TODAY
    day.mkdir(parents=True)
    (day / "portfolio.state").write_text("旧账", encoding="utf-8")
    gate = SessionGate(str(day))
    gate.mark_done(date(2026, 11, 3), "fp-t62")
    assert gate.check(date(2026, 11, 3), "fp-t62",
                      intraday_resume=True)["prior"] == "2026-11-03"   # 旧账能续跑

    ls.apply_reset(db, broker, reason="账本重开", today=TODAY, now=NOW,
                   state_root=str(root))
    after = SessionGate(str(day)).check(date(2026, 11, 3), "fp-t62",
                                        intraday_resume=True)
    assert after == {"allowed": True, "prior": None, "force": False}


# ==================================================== 钱类表：一根毛都不许动

def test_money_tables_are_untouched(broker, tmp_path):
    db = dirty_account(broker, tmp_path)
    before = ls._money_snapshot(sqlite3.connect(str(db)))  # noqa: SLF001
    out = ls.apply_reset(db, broker, reason="T62 测试", today=TODAY, now=NOW,
                         state_root=str(tmp_path / "var" / "rq_state"))
    assert out["money"] == before
    assert out["money"]["cash"] == pytest.approx(NEW_NAV)
    assert out["money"]["tables"]["orders"] == 0


def test_money_guard_rolls_back_when_a_target_oversteps(broker, monkeypatch, tmp_path):
    """将来谁往清单里塞了动钱类的项：当场红 + 回滚，目录也挪回来。"""
    db = dirty_account(broker, tmp_path)
    real_clear = ls._clear

    def evil(target, conn, ctx):
        res = real_clear(target, conn, ctx)
        if target.id == "risk_peak":
            conn.execute("UPDATE account SET cash = cash - 1 WHERE id = 1")
        return res

    monkeypatch.setattr(ls, "_clear", evil)
    with pytest.raises(RuntimeError, match="钱类现状被改动"):
        ls.apply_reset(db, broker, reason="越界测试", today=TODAY, now=NOW,
                       state_root=str(tmp_path / "var" / "rq_state"))
    cash = sqlite3.connect(str(db)).execute("SELECT cash FROM account WHERE id=1")
    assert cash.fetchone()[0] == pytest.approx(NEW_NAV)
    assert (tmp_path / "var" / "rq_state" / TODAY).is_dir()   # 目录挪回来了
    assert list((tmp_path / "var" / "rq_state").glob(f"{TODAY}.pre-reset-*")) == []
    assert rows(db, "SELECT COUNT(*) n FROM op_log WHERE action=?", (ls.ACTION,))[0]["n"] == 0


# ==================================================== 只报不清：不许假绿"清干净了"

def test_overdue_t1_freeze_is_reported_but_never_cleared(broker, tmp_path):
    """解冻是结算路径的活（要写 positions.available_volume），reset-state 无权代做。"""
    db = dirty_account(broker, tmp_path)
    plan = ls.plan(db, today=TODAY, now=NOW, state_root=str(tmp_path / "var" / "rq_state"))
    assert "t1_unfreeze_overdue" in plan["red_reports"]
    out = ls.apply_reset(db, broker, reason="T62 测试", today=TODAY, now=NOW,
                         state_root=str(tmp_path / "var" / "rq_state"))
    assert out["reports"]["t1_unfreeze_overdue"]["count"] == 1
    assert rows(db, "SELECT is_unfrozen FROM t1_freeze")[0]["is_unfrozen"] == 0


def test_report_items_cannot_be_sneaked_in_as_clearable(broker, tmp_path):
    """`--only` 点名只报不清的项 ⇒ 什么都清不掉（拼错/想偷懒都拦得住）。"""
    db = dirty_account(broker, tmp_path)
    out = ls.apply_reset(db, broker, reason="点名只报项", today=TODAY, now=NOW,
                         state_root=str(tmp_path / "var" / "rq_state"),
                         only=("t1_unfreeze_overdue", "intent_ledger_conflict"))
    assert out["no_op"] is True
    left = rows(db, "SELECT value FROM risk_state WHERE key='peak_total_value'")
    assert left and float(left[0]["value"]) == pytest.approx(OLD_PEAK)


def test_intent_rejected_without_fill_conflicts_with_the_ledger(broker, tmp_path):
    """切换日那三行的形状：意图标 rejected 说"没成交"，账本里成交还在 ⇒ 报红。"""
    db = broker.db_path
    bid = Q.enqueue(db, "002192", "buy", 100, rule_ref="rule:t62")
    Q.mark(db, bid, "rejected", {"note": "手工改判：no ledger fill"})
    with sqlite3.connect(str(db)) as c:
        # 用意图的**实际** created_at（enqueue 写的是运行当天；写死 TODAY 会
        # 让 JOIN 的同日条件落空——本测试曾因此日期 flaky，第三次同类）
        icreated = c.execute(
            "SELECT created_at FROM rq_intents WHERE biz_id=?",
            (bid,)).fetchone()[0]
        c.execute("INSERT INTO orders (symbol, direction, volume, order_type,"
                  " created_at, status, filled_volume, filled_price)"
                  " VALUES ('002192', 1, 100, 'market', ?, 'filled', 100, 44.94)",
                  (icreated,))
        c.commit()
    plan = ls.plan(db, today=TODAY, now=NOW, state_root=str(tmp_path / "none"))
    hit = [i for i in plan["items"] if i["id"] == "intent_ledger_conflict"][0]
    assert hit["count"] == 1 and bid in hit["samples"][0]


def test_idempotent_rejection_is_not_a_conflict(broker, tmp_path):
    """去重/幂等挡下的第二条形同"被拒"，与成交共存是正常形状——不许报成假红。"""
    db = broker.db_path
    bid = Q.enqueue(db, "002192", "buy", 100, rule_ref="rule:t62")
    Q.mark(db, bid, "rejected", {"reason": "同跳同款意图去重：只提交第一笔"})
    with sqlite3.connect(str(db)) as c:
        c.execute("INSERT INTO orders (symbol, direction, volume, order_type,"
                  " created_at, status, filled_volume, filled_price)"
                  " VALUES ('002192', 1, 100, 'market', ?, 'filled', 100, 44.94)",
                  (f"{TODAY}T16:49:26",))
        c.commit()
    plan = ls.plan(db, today=TODAY, now=NOW, state_root=str(tmp_path / "none"))
    assert [i for i in plan["items"]
            if i["id"] == "intent_ledger_conflict"][0]["count"] == 0


def test_state_keys_the_checklist_does_not_know_must_surface(broker, tmp_path):
    """纪律 7 的兜底：新代码写了新键而清单没跟上 ⇒ 冒出来，不许默默算清干净了。"""
    db = broker.db_path
    kv(db, "engine_state", "halt_reason:manual", '"someone pulled the plug"')
    kv(db, "risk_state", "blacklist:600000", "1")
    plan = ls.plan(db, today=TODAY, now=NOW, state_root=str(tmp_path / "none"))
    unk = [i for i in plan["items"] if i["id"] == "unknown_state_keys"][0]
    assert unk["count"] == 2
    assert "engine_state.halt_reason:manual" in unk["samples"]
    out = ls.apply_reset(db, broker, reason="T62 测试", today=TODAY, now=NOW,
                         state_root=str(tmp_path / "none"))
    assert out["reports"]["unknown_state_keys"]["count"] == 2   # 清完还在报


# ==================================================== CLI 接线（参数拼装也在射程里）

def _run_cli(monkeypatch, argv):
    """跑真的 `cli.main()`：模块级测试测不到 argparse 与 handler 之间的接线。"""
    from paper_trading import cli

    monkeypatch.setattr("sys.argv", ["paper_trading"] + argv)
    try:
        cli.main()
        return None                      # 正常结束不显式 exit
    except SystemExit as exc:
        return exc.code


def test_cli_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    db = tmp_path / "paper_account.db"
    PaperBroker(db_path=str(db), config=TradingConfig(initial_cash=NEW_NAV))
    RiskState(db).set("peak_total_value", f"{OLD_PEAK:.4f}")
    code = _run_cli(monkeypatch, ["--account-db", str(db), "--data-db",
                                  str(tmp_path / "data.db"),
                                  "ledger", "reset-state"])
    out = capsys.readouterr().out
    assert code is None                             # 没有红报 ⇒ 不该拿退出码吓人
    assert "risk_peak" in out and "dry-run" in out
    assert RiskState(db).get("peak_total_value")    # 没被清
    assert rows(db, "SELECT COUNT(*) n FROM op_log WHERE action=?",
                (ls.ACTION,))[0]["n"] == 0


def test_cli_yes_requires_a_reason_and_records_the_trail(tmp_path, monkeypatch, capsys):
    db = tmp_path / "paper_account.db"
    PaperBroker(db_path=str(db), config=TradingConfig(initial_cash=NEW_NAV))
    RiskState(db).set("peak_total_value", f"{OLD_PEAK:.4f}")
    base = ["--account-db", str(db), "--data-db", str(tmp_path / "data.db"),
            "ledger", "reset-state", "--yes"]
    assert _run_cli(monkeypatch, base) == 1         # 缺 --reason
    assert "reason" in capsys.readouterr().out      # 要说出来，不是静默退

    assert _run_cli(monkeypatch, base + ["--reason", "现网孤儿峰值",
                                         "--today", TODAY]) is None
    assert RiskState(db).get("peak_total_value") is None
    trail = rows(db, "SELECT params, result FROM op_log WHERE action=?", (ls.ACTION,))
    assert len(trail) == 1
    assert "现网孤儿峰值" in trail[0]["params"]
    assert json.loads(trail[0]["result"])["cleared"]["risk_peak"]["before"]


def test_cli_exit_one_when_only_report_items_are_red(tmp_path, monkeypatch, capsys):
    """红报必须影响退出码：脚本不许把 0 当成"状态已一致"的凭据。"""
    db = tmp_path / "paper_account.db"
    PaperBroker(db_path=str(db), config=TradingConfig(initial_cash=NEW_NAV))
    with sqlite3.connect(str(db)) as c:
        c.execute("INSERT INTO t1_freeze (symbol, volume, freeze_date,"
                  " unfreeze_date, is_unfrozen) VALUES ('002192', 100,"
                  " '2026-10-30', '2026-11-02', 0)")
        c.commit()
    code = _run_cli(monkeypatch, ["--account-db", str(db), "--data-db",
                                  str(tmp_path / "data.db"),
                                  "ledger", "reset-state", "--today", TODAY])
    out = capsys.readouterr().out
    assert code == 1 and "t1_unfreeze_overdue" in out


def test_cli_second_yes_run_does_not_claim_dry_run(tmp_path, monkeypatch, capsys):
    """现网试跑照出来的失真：no-op 那次印了"dry-run（--yes 才动手）"，
    而 --yes 明明给了——操作者会以为自己漏了参数。标签必须跟着事实走。"""
    db = tmp_path / "paper_account.db"
    PaperBroker(db_path=str(db), config=TradingConfig(initial_cash=NEW_NAV))
    RiskState(db).set("peak_total_value", f"{OLD_PEAK:.4f}")
    argv = ["--account-db", str(db), "--data-db", str(tmp_path / "data.db"),
            "ledger", "reset-state", "--yes", "--today", TODAY, "--reason", "试跑"]
    _run_cli(monkeypatch, argv)
    capsys.readouterr().out
    _run_cli(monkeypatch, argv)
    out = capsys.readouterr().out
    assert "dry-run" not in out and "无可清的运行态" in out


# ==================================================== 幂等 / 选项 / 清单本身

def test_second_run_is_a_no_op_and_writes_no_second_trail(broker, tmp_path):
    db = dirty_account(broker, tmp_path)
    root = str(tmp_path / "var" / "rq_state")
    first = ls.apply_reset(db, broker, reason="T62 测试", today=TODAY, now=NOW,
                           state_root=root)
    second = ls.apply_reset(db, broker, reason="T62 测试", today=TODAY,
                            now=datetime(2026, 11, 3, 11, 0, 0), state_root=root)
    assert first["applied"] is True and first["cleared"] > 0
    assert second["no_op"] is True and second["cleared"] == 0
    assert rows(db, "SELECT COUNT(*) n FROM op_log WHERE action=?", (ls.ACTION,))[0]["n"] == 1


def test_only_targets_one_item_and_leaves_the_rest(broker, tmp_path):
    db = dirty_account(broker, tmp_path)
    out = ls.apply_reset(db, broker, reason="只清峰值", today=TODAY, now=NOW,
                         state_root=str(tmp_path / "var" / "rq_state"),
                         only=("risk_peak",))
    assert out["cleared_by_target"] == {"risk_peak": 1}
    assert rows(db, "SELECT COUNT(*) n FROM risk_state WHERE key LIKE 'daily_baseline:%'")[0]["n"] == 2
    assert len(Q.pending(db)) == 1


def test_keep_refuses_an_item_and_says_so_in_the_trail(broker, tmp_path):
    db = dirty_account(broker, tmp_path)
    out = ls.apply_reset(db, broker, reason="条件单要留", today=TODAY, now=NOW,
                         state_root=str(tmp_path / "var" / "rq_state"),
                         keep=("cond_armed",))
    assert "cond_armed" not in out["cleared_by_target"]
    assert rows(db, "SELECT status FROM conditional_orders")[0]["status"] == "armed"
    trail = json.loads(rows(db, "SELECT result FROM op_log WHERE action=?",
                            (ls.ACTION,))[0]["result"])
    assert trail["kept"] == ["cond_armed"]


def test_unknown_option_id_is_refused_not_ignored(broker):
    """--keep rsk_peak 这种拼写错误如果静默放过，就是"以为清了其实没清"。"""
    with pytest.raises(ValueError, match="清单里没有"):
        ls.selected(keep=("rsk_peak",))
    with pytest.raises(ValueError, match="清单里没有"):
        ls.selected(only=("peak_total_value",))


def test_plan_is_read_only(broker, tmp_path):
    db = dirty_account(broker, tmp_path)
    before = rows(db, "SELECT COUNT(*) n FROM op_log")[0]["n"]
    plan = ls.plan(db, today=TODAY, now=NOW, state_root=str(tmp_path / "var" / "rq_state"))
    assert set(plan["pending"]) >= {"risk_peak", "risk_daily_baseline",
                                    "rq_intent_pending", "cond_armed",
                                    "rq_session_state", "engine_fired"}
    assert RiskState(db).get("peak_total_value")                  # 没被清
    assert rows(db, "SELECT COUNT(*) n FROM op_log")[0]["n"] == before


def test_every_required_item_has_probe_and_clear(broker, tmp_path):
    """清单完整性：required 项必须真能探到、真能清——写一半的项比没有更危险。"""
    db = dirty_account(broker, tmp_path)
    root = str(tmp_path / "var" / "rq_state")
    plan = ls.plan(db, today=TODAY, now=NOW, state_root=root)
    required = [i for i in plan["items"] if i["required"]]
    assert {i["id"] for i in required} == {t.id for t in ls.TARGETS if t.required}
    for i in required:                       # 摆出来的现场必须每一项都探得到
        assert i["count"] >= 1, i["id"]
    out = ls.apply_reset(db, broker, reason="全清", today=TODAY, now=NOW,
                         state_root=root)
    assert out["applied"] is True
    again = ls.plan(db, today=TODAY, now=NOW, state_root=root)
    assert [i["id"] for i in again["items"] if i["will_clear"]] == []


def test_checklist_ids_are_unique_and_doc_matches_code():
    """纪律 7：文档表格由清单渲染，测试把两边的 id 集合对死——谁先漂移谁红。"""
    import re

    ids = ls.target_ids()
    assert len(ids) == len(set(ids))
    assert all(t.kind in (ls.KIND_HALT, ls.KIND_GHOST, ls.KIND_THROTTLE,
                          ls.KIND_REPORT) for t in ls.TARGETS)
    doc = (Path(__file__).resolve().parents[1] / "docs" / "migration-m3.md").read_text(
        encoding="utf-8")
    section = doc.split("重置后必清", 1)[1].split("用法（默认 dry-run", 1)[0]
    doc_ids = re.findall(r"^\| `([a-z0-9_]+)` \|", section, flags=re.M)
    assert doc_ids == ids, f"文档表与代码清单漂移：只在文档={set(doc_ids)-set(ids)} " \
                           f"只在代码={set(ids)-set(doc_ids)} 顺序={doc_ids} vs {ids}"
