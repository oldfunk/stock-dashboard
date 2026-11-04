"""2026-11-04 现网 day-1 两个 P0 的回归测试 + 复核时补的第三处同类漏项。

主 AI 的修复（`d1a487d`）没有带测试。没有回归测试的钱类修复，等于只修了今天：
下一条新路径、另一次重构，会把它原样再犯一遍。这三条都是同一个形状——
**新底座分叉出去的那条分支，漏掉了旧分支上的某个副作用**：

1. `engine executor=rqalpha` 分支漏 `mark_order` ⇒ 同规则每分钟重复入队
   （现网 300390 一天 19 条，18 条被标 executed 却无成交）；
2. 同一条分支漏 `bump_budget` ⇒ `max_orders_per_day/max_turnover_per_day`
   在 rqalpha 底座下形同虚设（条件单分支一直在加，两条路径共用一个计数器
   却只有一家在累加）——这条是复核 d1a487d 时照出来的，本次一并修；
3. `rq-nav` 让位检查只看 `op_log` ⇒ RunDaily 直接写 `nav_history` 不留痕，
   同日两行 NAV（现网双写事故）。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.broker.paper_broker import PaperBroker        # noqa: E402
from paper_trading.cli import TradingBridge                       # noqa: E402
from paper_trading.engine.config import DEFAULTS                  # noqa: E402
from paper_trading.engine.runner import EngineRunner              # noqa: E402
from paper_trading.integration.rq_engine import ledger as ld       # noqa: E402
from paper_trading.models import TradingConfig                      # noqa: E402

DAY = "2026-11-04"


def _rule(rid, sym, side="buy", value=6000.0):
    return {"id": rid, "type": "price_level", "symbol": sym,
            "params": {"op": "below", "value": 41.0},
            "action": {"side": side, "value": value}, "once": False}


def _cfg(**over):
    cfg = dict(DEFAULTS)
    cfg.update({"enabled": True, "universe": "list", "executor": "rqalpha",
                "min_ticket_cny": 0.0, "max_orders_per_day": 6,
                "max_turnover_per_day": 0.0, "per_symbol_cooldown_sec": 0,
                "rules": [_rule("r-a", "600036")]})
    cfg.update(over)
    return cfg


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    b = TradingBridge(data_db=str(tmp_path / "data.db"),
                      account_db=str(tmp_path / "acct.db"),
                      config_path=str(tmp_path / "nope.yaml"),
                      secrets_path=str(tmp_path / "s.json"),
                      config=TradingConfig(initial_cash=1_000_000.0))
    from paper_trading.data import realtime as rt
    monkeypatch.setattr(rt, "is_trading_session", lambda *a, **k: True)
    monkeypatch.setattr(rt, "get_quotes",
                        lambda syms, force=False, **k: {
                            str(s): {"price": 40.0, "prev_close": 39.0,
                                    "change_pct": 2.5, "high": 40.5, "low": 39.5,
                                    "volume": 100000, "turnover": 4e6}
                            for s in syms})
    return b


def intents(bridge):
    with sqlite3.connect(str(bridge.account_db_path)) as c:
        return c.execute("SELECT biz_id, symbol, side, volume, status"
                         " FROM rq_intents ORDER BY biz_id").fetchall()


def budget(bridge):
    with sqlite3.connect(str(bridge.account_db_path)) as c:
        row = c.execute("SELECT value FROM engine_state WHERE key LIKE 'budget:%'").fetchone()
    import json
    return json.loads(row[0]) if row else {"orders": 0, "turnover": 0.0}


# ------------------------------------------------ P0-1：入队必须防重

def test_rq_enqueue_marks_the_symbol_so_the_rule_does_not_reflood(bridge):
    """`mark_order` 漏一次，现网就一天灌 19 条同 key 意图。

    冷却用现网值 900 秒、规则 `once=False`：跨分钟那一挡**只能**由入队时写
    mark_order 提供（就是 d1a487d 补的那一行）。冷却=0 时这条防线本来就不存在
    （`if last and cooldown > 0` 直接跳过），所以不能拿 0 来测。
    """
    r = EngineRunner(bridge, cfg=_cfg(per_symbol_cooldown_sec=900))
    first = r.tick()
    assert len(first["fired"]) == 1 and first["fired"][0].get("queued") is True
    second = EngineRunner(bridge, cfg=_cfg(per_symbol_cooldown_sec=900)).tick()
    assert second["fired"] == [], second
    assert any("冷却中" in s.get("why", "") for s in second["skipped"]), second
    assert len(intents(bridge)) == 1, intents(bridge)


def test_rq_fork_does_not_touch_the_ledger(bridge):
    """底座是 rqalpha 时，引擎这一跳只排队，绝不自己写成交（纪律 1 的边界）。"""
    EngineRunner(bridge, cfg=_cfg()).tick()
    with sqlite3.connect(str(bridge.account_db_path)) as c:
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    assert intents(bridge), "意图必须入队，否则判定完没人执行"


# ------------------------------------------- P0-2（复核补）：入队必须累加预算

def test_rq_enqueue_consumes_the_daily_order_budget(bridge):
    """两条规则、两个标的、`max_orders_per_day=1` ⇒ 第二条必须被限额挡下。

    修之前这条断言会红：规则分支只排队不 bump_budget，
    限额只被条件单分支累加，新底座下"日内报单笔数上限"实际不生效。
    """
    # 预算是**每轮开头读一次**（两条路径都一样，见报告里的"同轮内不重读"观察），
    # 所以跨轮才看得出限额真的在累加：先 r-a 占掉唯一的额度，再来一轮换 r-b。
    cfg = _cfg(max_orders_per_day=1, rules=[_rule("r-a", "600036")])
    first = EngineRunner(bridge, cfg=cfg).tick()
    assert len(first["fired"]) == 1, first
    assert budget(bridge)["orders"] == 1
    again = EngineRunner(bridge, cfg=_cfg(max_orders_per_day=1,
                                          rules=[_rule("r-b", "000792")])).tick()
    assert again["fired"] == [], again
    assert any("日内笔数上限" in s.get("why", "") for s in again["skipped"]), again


def test_rq_enqueue_counts_turnover_toward_the_daily_cap(bridge):
    cfg = _cfg(max_turnover_per_day=1000)          # 40.0×100=4000 元 > 1000
    out = EngineRunner(bridge, cfg=cfg).tick()
    assert out["fired"] == []
    assert any("日内金额上限" in s.get("why", "") for s in out["skipped"]), out
    assert budget(bridge)["turnover"] == 0.0, "被挡下的一律不许占用预算"


# ------------------------------------------- P0-3：nav 让位要看 nav_history

@pytest.fixture()
def broker(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "paper_account.db"),
                       config=TradingConfig(initial_cash=100_000.0))


def test_rq_nav_yields_to_history_rows_without_op_log(broker):
    """RunDaily 那类写入者直接写 `nav_history` 且不留 `op_log`——
    只查 op_log 的让位检查看不见它，同日就会两行 NAV（现网双写事故）。"""
    from paper_trading.models import AccountSnapshot
    broker.record_nav(AccountSnapshot(timestamp=datetime.fromisoformat(f"{DAY}T15:30:00"), cash=100_000.0,
                                      market_value=0.0, total_value=100_000.0,
                                      available_cash=100_000.0, pnl=0.0,
                                      pnl_pct=0.0))
    out = ld.record_close_nav(broker, {}, today=DAY, fp="fp-rq")
    assert out["recorded"] is False and out["reason"] == "already_recorded"
    with sqlite3.connect(str(broker.db_path)) as c:
        assert c.execute("SELECT COUNT(*) FROM nav_history WHERE timestamp LIKE ?",
                         (f"{DAY}%",)).fetchone()[0] == 1


def test_rq_nav_still_records_when_history_is_empty(broker):
    """反向也要成立：让位检查不能把"该记的一天不断供"这条 G2 规矩吃掉。"""
    out = ld.record_close_nav(broker, {}, today=DAY, fp="fp-rq")
    assert out["recorded"] is True and out["today"] == DAY


def test_nav_history_helper_is_fail_open_and_uses_a_read_only_uri(broker, tmp_path,
                                                                  caplog):
    """查不到按"没有"处理（宁可多记也不许当天净值断供）；且必须只读 + **要 warn**。

    这处 fail-open 直接决定同日会不会写进行 NAV（T69 审查点名的就是它）：
    静默的话，让位失败只会表现为"面板上那天两行净值"，事后没人知道为什么。
    """
    import inspect
    import logging

    src = inspect.getsource(ld._nav_history_has_today)
    assert "mode=ro" in src and "uri=True" in src
    broker.db_path = tmp_path / "gone.db"
    with caplog.at_level(logging.WARNING):
        assert ld._nav_history_has_today(broker, DAY) is False
    assert "nav_history 当日行检查失败" in caplog.text


def test_corrupt_cooldown_timestamp_warns_instead_of_silently_opening_the_gate(
        bridge, caplog):
    """冷却时间戳坏掉 = P0-1 那道防线静默消失；不吭声就等于把重灌的口子留在那儿。"""
    import logging

    from paper_trading.engine.state import EngineState
    EngineState(bridge.account_db_path).set("cooldown:600036", "昨天下午三点")
    with caplog.at_level(logging.WARNING):
        out = EngineRunner(bridge, cfg=_cfg(per_symbol_cooldown_sec=900)).tick()
    assert len(out["fired"]) == 1, out          # 行为：不拦单（宁可放行）
    assert "冷却时间戳读不出" in caplog.text     # 但必须留下声音
