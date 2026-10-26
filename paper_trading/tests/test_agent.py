"""AI 交易员单测（fake LLM + 禁网，离线可跑，覆盖钳制/幂等/坏输出）。"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent import AgentConfig, AgentTrader  # noqa: E402
from paper_trading.llm.provider import LLMConfig  # noqa: E402
from paper_trading.models import Bar, Order, OrderType  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """禁网铁律：单测永不碰真实行情源（有网环境会拉回真数据污染断言）。"""
    from paper_trading.cli import TradingBridge

    monkeypatch.setattr(
        TradingBridge, "sync_data",
        lambda self, symbols: {"ok": True, "symbols": symbols, "updated": {}})


@pytest.fixture(autouse=True)
def _sched_due(monkeypatch):
    """定时闸固定在 17:00（默认 16:45 时段已到），历史用例不受真实时钟影响。"""
    from paper_trading.agent import schedule as sch

    monkeypatch.setattr(sch, "_now", lambda: datetime(2026, 9, 29, 17, 0))


def mkbar(sym, day, close):
    return Bar(symbol=sym, timestamp=datetime(2024, 1, 1) + timedelta(days=day),
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def _bridge(tmp, bars):
    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                        secrets_path=str(Path(tmp) / "s.json"))
    b.data_db.upsert_bars(bars)
    b.llm_save("custom", "https://x/v1", "m", "k")  # fake 源，chat 由单测替换
    return b


def _fresh_bars(n=30, base=10.0):
    return [mkbar("600519", 10_000 + i, base + i * 0.1) for i in range(n)]
    # day 10000+ → 日期远超今天，保证“新鲜”守卫通过


def test_agent_buy_fills_and_logs(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    plan = {"actions": [{"action": "buy", "symbol": "600519",
                         "volume": 100, "price": None, "reason": "测试"}],
            "summary": "买一手试试"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0))
    res = t.run(["600519"])
    assert res["ok"] and len(res["decisions"]) == 1
    assert res["decisions"][0]["status"].startswith("已成交")
    assert b.broker.get_position("600519").total_volume == 100
    ops = b.broker.get_op_log(5)
    assert ops[0]["action"] == "ai:decide" and ops[0]["ok"] == 1


def test_agent_rejects_bad_actions(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    plan = {"actions": [
        {"action": "buy", "symbol": "999999", "volume": 100, "reason": "池外"},
        {"action": "buy", "symbol": "600519", "volume": 50, "reason": "零股"},
        {"action": "dance", "symbol": "600519", "volume": 100, "reason": "乱动"},
    ], "summary": "全拒"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    res = t.run(["600519"])
    assert res["ok"]
    assert all("拒绝" in d["status"] for d in res["decisions"])
    assert b.broker.get_order_history(10) == [] or all(
        o["status"] == "rejected" for o in b.broker.get_order_history(10))


def test_agent_sells_holding_outside_pool(monkeypatch, tmp_path):
    """持仓退出筛选池后仍能卖：卖出白名单来自真实持仓，不是本轮池。"""
    from paper_trading.llm import provider as prov

    bars = _fresh_bars() + [mkbar("000001", 10_000 + i, 10.0) for i in range(30)]
    b = _bridge(str(tmp_path), bars)
    b.broker.submit_order(Order(
        symbol="600519", direction=1, volume=100, order_type=OrderType.LIMIT,
        limit_price=12.9, prev_close=12.9, ref_high=13.5, ref_low=12.5))
    b.broker.unfreeze_t1(datetime.now() + timedelta(days=10))
    assert b.broker.get_position("600519").available_volume == 100
    plan = {"actions": [{"action": "sell", "symbol": "600519", "volume": 100,
                         "reason": "池外持仓离场"}], "summary": "卖池外持仓"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    res = AgentTrader(b, AgentConfig(picks_enabled=False)).run(["000001"])
    assert res["ok"]
    assert res["decisions"][0]["status"].startswith("已成交")
    assert b.broker.get_position("600519") is None


def test_agent_arms_ai_conditions(monkeypatch, tmp_path):
    """决策 JSON 里的 conditions 现在挂到条件单仓库，由引擎盘中触发。"""
    from paper_trading.engine.conditional import ConditionalOrders
    from paper_trading.llm import provider as prov

    exp = (datetime.now() + timedelta(days=2)).date().isoformat()
    plan = {"actions": [], "conditions": [{
        "symbol": "600519", "side": "buy",
        "cond": {"type": "price_below", "value": 10.5},
        "value": 8000, "expiry": exp, "reason": "回踩支撑"}],
        "summary": "挂条件"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    res = AgentTrader(b, AgentConfig(picks_enabled=False)).run(["600519"])
    assert res["ok"] and res["conditions"]["n_created"] == 1
    o = ConditionalOrders(b.account_db_path).list_all(5)[0]
    assert o["status"] == "armed" and o["source"] == "ai"
    assert o["cond_type"] == "price_below" and o["cond_value"] == 10.5


def test_ai_condition_fills_through_engine(monkeypatch, tmp_path):
    """端到端：AI 挂条件 → ConditionRunner 盘中触发 → 成交（不是二次下单）。"""
    from paper_trading.engine.conditional import ConditionalOrders
    from paper_trading.engine.runner import ConditionRunner
    from paper_trading.llm import provider as prov

    exp = (datetime.now() + timedelta(days=2)).date().isoformat()
    plan = {"actions": [], "conditions": [{
        "symbol": "600519", "side": "buy",
        "cond": {"type": "price_below", "value": 10.5},
        "value": 8000, "expiry": exp, "reason": "回踩买入"}],
        "summary": "条件单"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    assert t.run(["600519"])["conditions"]["n_created"] == 1
    quotes = {"600519": {"code": "600519", "price": 10.4, "prev_close": 10.5,
                         "high": 10.6, "low": 10.3, "volume": 1e6,
                         "change_pct": -1.0}}
    r = ConditionRunner(b, ConditionalOrders(b.account_db_path),
                        min_ticket_cny=0).tick(quotes)
    assert r["filled"], r
    assert b.broker.get_position("600519").total_volume > 0


def test_agent_plan_conditions_arm_on_execute(monkeypatch, tmp_path):
    """计划阶段只保存条件；到目标交易日执行计划时才挂单。"""
    from paper_trading.engine.conditional import ConditionalOrders
    from paper_trading.llm import provider as prov

    exp = (datetime.now() + timedelta(days=3)).date().isoformat()
    plan = {"actions": [], "conditions": [{
        "symbol": "600519", "side": "buy",
        "cond": {"type": "price_above", "value": 13.5},
        "value": 8000, "expiry": exp, "reason": "突破"}],
        "summary": "计划条件"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    r = t.run(["600519"], plan_only=True)
    co = ConditionalOrders(b.account_db_path)
    assert co.list_all(5) == []          # 计划不提前挂单
    pend = b.broker.get_pending_plan(r["plan_date"])
    assert pend["plan"]["conditions"]
    res = t._execute_plan(pend, {"600519": b.data_db.get_bars("600519", limit=30)},
                          b.price_map(["600519"]))
    assert res["conditions"]["n_created"] == 1
    assert co.list_all(5)[0]["status"] == "armed"


def test_plan_condition_expiry_bumped_to_execution_day(monkeypatch, tmp_path):
    """跨周计划：条件 expiry 落在执行日前（如周六）→ 顺延到执行日并留痕。

    日期全部相对今天构造，避免“运行日恰好在执行日”时不再触发顺延
    （exp == exec_day 是合法的“覆盖到当天”，不该顺延——测试别赌运行日）。
    """
    from paper_trading.engine.conditional import ConditionalOrders
    from paper_trading.llm import provider as prov

    today = datetime.now().date()
    exp = (today + timedelta(days=1)).isoformat()
    exec_day = (today + timedelta(days=3)).isoformat()
    plan = {"actions": [], "conditions": [{
        "symbol": "600519", "side": "buy",
        "cond": {"type": "price_below", "value": 10.5},
        "value": 8000, "expiry": exp, "reason": "回踩"}],
        "summary": "跨周计划"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    r = t.run(["600519"], plan_only=True)
    pend = b.broker.get_pending_plan(r["plan_date"])
    assert pend["plan"]["conditions"][0]["expiry"] == exp  # 存档保留原始值
    res = t._execute_plan(pend, {"600519": b.data_db.get_bars("600519", limit=30)},
                          b.price_map(["600519"]), exec_day=exec_day)
    assert res["conditions"]["n_created"] == 1
    o = ConditionalOrders(b.account_db_path).list_all(5)[0]
    assert o["expiry"].startswith(exec_day)         # 顺延到执行日
    assert "顺延" in (o["note"] or "")                # 调整留痕可审计


def test_close_nav_snapshot_once_per_day(tmp_path):
    """收盘计划窗口记一条收盘 NAV（nav:close 幂等），避免 NAV 序列空洞。"""
    import json as _json

    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    t._snapshot_close_nav(["600519"], "2026-10-23")
    t._snapshot_close_nav(["600519"], "2026-10-23")     # 同日重复调用不重复记
    navs = b.get_nav_history()
    assert len(navs) == 1
    ops = [o for o in b.broker.get_op_log(10) if o["action"] == "nav:close"]
    assert len(ops) == 1
    assert _json.loads(ops[0]["params"])["symbols"] == 1


def test_nav_close_idempotent_for_explicit_past_day(tmp_path):
    """幂等按业务日判断：day 不等于今天时也不能重复记（Issue #2 回归）。

    写入时间戳永远是 today，只有 result.date 记住业务日；只按 timestamp
    匹配会让补记历史日/次日重跑永远查不到，从而重复插 nav_history。
    """
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    t._snapshot_close_nav(["600519"], "2020-01-02")
    t._snapshot_close_nav(["600519"], "2020-01-02")
    assert b.broker.has_operation("nav:close", "2020-01-02")
    assert not b.broker.has_operation("nav:close", "2020-01-03")
    assert len(b.get_nav_history()) == 1


def test_plan_exists_still_snapshots_close_nav(tmp_path):
    """计划已存在（其他入口写入/上次快照失败）时仍要补记当日 NAV。"""
    from paper_trading.utils.trading_calendar import next_trading_day

    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    plan_date = next_trading_day(datetime.now()).isoformat()
    b.broker.save_plan(plan_date, ["600519"], {"actions": [], "summary": ""})
    today = datetime.now().date().isoformat()

    r = t._plan(["600519"], require_today=True)
    assert r["skipped"] == "plan-exists"
    assert b.broker.has_operation("nav:close", today)
    assert len(b.get_nav_history()) == 1

    t._plan(["600519"], require_today=True)             # 下一个 tick 不重复记
    assert len(b.get_nav_history()) == 1


def test_arm_rejects_sell_condition_without_holding(tmp_path):
    """挂单层兜底：给非持仓挂卖出条件必须拒（计划 #12 暴露的口子）。

    计划是过夜保存的：解析时点的持仓可能到执行时点已变化，所以
    `_arm_conditions` 是最后一道闸——宁可拒掉也绝不挂必然失败的卖单。
    """
    from paper_trading.engine.conditional import ConditionalOrders

    exp = (datetime.now() + timedelta(days=2)).date().isoformat()
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    conds = [{"symbol": "600519", "side": "sell", "cond_type": "price_below",
              "cond_value": 10.0, "volume": 100, "value": 0,
              "expiry": f"{exp}T23:59", "note": "非持仓卖出", "source": "ai"}]
    res = t._arm_conditions(conds)
    assert res["n_created"] == 0 and res["n_rejected"] == 1
    assert "持仓" in res["rejected"][0]["why"]
    assert ConditionalOrders(b.account_db_path).list_all(5) == []


def test_trader_prompt_has_exit_playbook():
    """离场三件套（止损/放飞/清仓）必须写进系统提示，且不破坏 JSON 占位符。"""
    from paper_trading.agent.prompts import TRADER_SYSTEM

    system = TRADER_SYSTEM.format(
        max_order_value=20000, max_orders=3, max_conditions=5,
        condition_max_days=3, min_ticket=5000)
    for kw in ("离场纪律", "止损", "放飞", "清仓", "什么情况就卖",
               "卖出条件只能挂在当前持仓", "price_below 卖出条件",
               "price_above 卖出条件"):
        assert kw in system, kw
    assert '"actions"' in system          # JSON 模板完好


def test_agent_garbage_output_safe(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": "今天天气不错！！")
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    res = t.run(["600519"])
    assert res["ok"] and res["decisions"] == []


def test_agent_idempotent_same_day(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    plan = {"actions": [], "summary": "不动"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    assert t.run(["600519"])["ok"]
    res2 = t.run(["600519"])
    assert res2.get("skipped") == "already-decided"
    # 跳过也记流水（面板可见“今日已决策”）
    assert any("already-decided" in (o["result"] or "")
               for o in b.broker.get_op_log(10) if o["action"] == "ai:decide")


def test_agent_skip_does_not_lock_day(monkeypatch, tmp_path):
    """no-fresh-bars 这类跳过不算实质决策，不锁死当日后来的真跑。"""
    from paper_trading.llm import provider as prov

    plan = {"actions": [], "summary": "不动"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    assert not t._decided_today()
    b.broker.log_operation("ai:decide", {"symbols": ["600519"]}, True,
                           {"skipped": "no-fresh-bars"}, None, None)
    assert not t._decided_today()  # 跳过不锁
    assert t.run(["600519"])["ok"]  # 真跑仍可执行（fake bars 新鲜）
    assert t._decided_today()  # 实质决策后锁定


def test_agent_llm_timeout_retries_once(monkeypatch, tmp_path):
    """超时重试一次：第一次超时、第二次成功则整体成功。"""
    from paper_trading.agent import loop as agent_loop
    from paper_trading.llm import LLMError, provider as prov

    calls = {"n": 0}

    def flaky(cfg, messages, system=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("请求失败：TimeoutError: timed out")
        return '{"actions": [], "summary": "ok"}'

    monkeypatch.setattr(prov, "chat", flaky)
    cfg = AgentConfig(llm_timeout=5.0, llm_retries=1)
    out = agent_loop._ask_llm(
        LLMConfig(base_url="https://x/v1", api_key="k", model="m"),
        "hi", "sys", cfg.llm_timeout, cfg.llm_retries)
    assert out.startswith("{") and calls["n"] == 2

    def always_timeout(cfg, messages, system=""):
        raise LLMError("TimeoutError: timed out")

    monkeypatch.setattr(prov, "chat", always_timeout)
    try:
        agent_loop._ask_llm(
            LLMConfig(base_url="https://x/v1", api_key="k", model="m"),
            "hi", "sys", 5.0, 1)
        raise AssertionError("should raise")
    except LLMError:
        pass


def test_agent_dry_run_changes_nothing(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    plan = {"actions": [{"action": "buy", "symbol": "600519",
                         "volume": 100, "price": None, "reason": "试"}], "summary": ""}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    res = t.run(["600519"], dry_run=True)
    assert res["ok"] and "试运行" in res["decisions"][0]["status"]
    assert b.broker.get_position("600519") is None


def test_agent_plan_then_execute(monkeypatch, tmp_path):
    """休盘做计划（不碰账本）→ 开盘执行计划（成交并标记done）。"""
    from paper_trading.llm import provider as prov

    plan = {"actions": [{"action": "buy", "symbol": "600519",
                         "volume": 100, "price": None, "reason": "计划买"}],
            "summary": "休盘计划"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig())
    r1 = t.run(["600519"], plan_only=True)
    assert r1["ok"] and r1["mode"] == "plan" and r1["plan_id"] > 0
    assert b.broker.get_position("600519") is None  # 做计划不碰账本
    assert b.broker.get_order_history(10) == []
    # 计划写的是下一个交易日，不是今晚；17:15 的 trade 不能提前消费。
    assert r1["plan_date"] > datetime.now().date().isoformat()
    pend = b.broker.get_pending_plan(r1["plan_date"])
    assert pend and pend["status"] == "pending"
    assert b.broker.get_executable_plan(datetime.now().date().isoformat()) is None
    # 模拟目标交易日开盘：执行计划（不再问 LLM）
    bars = {"600519": b.data_db.get_bars("600519", limit=30)}
    latest = b.price_map(["600519"])
    r2 = t._execute_plan(pend, bars, latest)
    assert r2["ok"] and r2.get("from_plan") is True
    assert r2["decisions"][0]["status"].startswith("已成交")
    assert b.broker.get_position("600519").total_volume == 100
    assert b.broker.get_pending_plan(r1["plan_date"]) is None  # 已消费
    # 同日再存计划，旧 pending 自动作废，只剩最新
    b.broker.save_plan(__import__("datetime").date.today().isoformat(),
                       ["600519"], {"actions": [], "summary": "新计划"})
    b.broker.save_plan(__import__("datetime").date.today().isoformat(),
                       ["600519"], {"actions": [], "summary": "更新计划"})
    pend2 = b.broker.get_pending_plan(
        __import__("datetime").date.today().isoformat())
    assert pend2["plan"]["summary"] == "更新计划"


def test_agent_executes_plan_on_stale_bars(monkeypatch, tmp_path):
    """计划执行不受新鲜守卫限制（休盘计划开盘执行正是为此设计）。"""
    import json as _json
    from paper_trading.llm import provider as prov

    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": _json.dumps(
        {"actions": [], "summary": "不应被调用"}))
    b = _bridge(str(tmp_path), [mkbar("600519", i, 10 + i) for i in range(30)])
    # 手工存一条今日计划（绕过 LLM），bars 是 2024 年旧数据
    b.broker.save_plan(__import__("datetime").date.today().isoformat(), ["600519"],
                       {"actions": [{"action": "buy", "symbol": "600519",
                                     "volume": 100, "reason": "旧计划"}],
                        "summary": "旧", "asof": "2024-01-30"})
    t = AgentTrader(b, AgentConfig())
    res = t.run(["600519"])
    assert res["ok"] and res.get("from_plan") is True
    assert res["decisions"][0]["status"].startswith("已成交")


def test_agent_executes_previous_day_plan(tmp_path):
    """早盘 trade 消费前一交易日计划（plan_date 可以是昨天）。"""
    from datetime import date, timedelta

    b = _bridge(str(tmp_path), _fresh_bars())
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    pid = b.broker.save_plan(yesterday, ["600519"], {
        "actions": [{"action": "buy", "symbol": "600519", "volume": 100,
                     "price": 12.9, "price_source": "last_close",
                     "reason": "昨日计划"}],
        "summary": "昨日计划", "asof": yesterday})
    assert pid > 0
    r = AgentTrader(b, AgentConfig(picks_enabled=False)).run(["600519"])
    assert r["ok"] and r.get("from_plan") is True
    assert r["decisions"][0]["status"].startswith("已成交")
    assert b.broker.get_position("600519").total_volume == 100
    assert b.broker.get_pending_plan(yesterday) is None


def test_agent_plan_backfills_price_from_last_close(monkeypatch, tmp_path):
    """计划里的 buy/sell 必须落定稿收盘价，不能把 null 留给执行链。"""
    from paper_trading.llm import provider as prov

    plan = {"actions": [{"action": "buy", "symbol": "600519", "volume": 100,
                         "price": None, "reason": "计划买"}],
            "summary": "计划"}
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": json.dumps(plan))
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    r = t.run(["600519"], plan_only=True)
    assert r["ok"] and r["mode"] == "plan"
    pend = b.broker.get_pending_plan(r["plan_date"])
    act = pend["plan"]["actions"][0]
    assert act["price"] == 12.9
    assert act["price_source"] == "last_close"


def test_execute_plan_covers_plan_only_symbol_and_applies_cap(monkeypatch, tmp_path):
    """计划新增标的必须在执行上下文补价；否则 0 价会绕过单笔金额上限。"""
    b = _bridge(str(tmp_path),
                [mkbar("600519", 10_000 + i, 1309.69) for i in range(30)])
    today = datetime.now().date().isoformat()
    b.broker.save_plan(today, ["600519"], {
        "actions": [{"action": "buy", "symbol": "600519", "volume": 100,
                     "price": None, "reason": "计划新增"}],
        "summary": "计划新增"})
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3,
                                   max_order_value=20000.0,
                                   picks_enabled=False))
    res = t.run(["000001"])  # 本轮运行池不含 600519
    assert res["ok"] and res.get("from_plan") is True
    assert "超单笔上限" in res["decisions"][0]["status"]
    assert b.broker.get_order_history(10) == []


def test_execute_plan_rejects_missing_price(monkeypatch, tmp_path):
    """无最新价、无可信计划价时必须显式拒单，不能把 0 传给撮合。"""
    b = _bridge(str(tmp_path),
                [mkbar("000001", i, 10.0) for i in range(5)])
    today = datetime.now().date().isoformat()
    b.broker.save_plan(today, ["600519"], {
        "actions": [{"action": "buy", "symbol": "600519", "volume": 100,
                     "price": None, "reason": "无价"}],
        "summary": "无价"})
    t = AgentTrader(b, AgentConfig(max_order_value=20000.0))
    res = t.run(["000001"])
    assert res["decisions"][0]["status"] == "拒绝：无有效价格"
    assert b.broker.get_order_history(10) == []


def test_execute_plan_uses_verified_basis_when_latest_missing(monkeypatch, tmp_path):
    """只有 price_source=last_close 的计划价允许在缺最新价时兜底。"""
    b = _bridge(str(tmp_path),
                [mkbar("000001", i, 10.0) for i in range(5)])
    today = datetime.now().date().isoformat()
    b.broker.save_plan(today, ["600519"], {
        "actions": [{"action": "buy", "symbol": "600519", "volume": 100,
                     "price": 10.0, "price_source": "last_close",
                     "reason": "定稿基准价"}],
        "summary": "基准价"})
    t = AgentTrader(b, AgentConfig(max_order_value=20000.0))
    res = t.run(["000001"])
    assert res["decisions"][0]["status"].startswith("已成交")
    assert b.broker.get_position("600519").total_volume == 100


def test_stock_pool_yaml_octal_guard(tmp_path):
    """000333 这类全小数字 YAML 会吞成八进制 int；引号+归一化必须保住原码。"""
    from paper_trading.utils.config import load_config

    f = tmp_path / "c.yaml"
    f.write_text("stock_pool:\n  - \"000333\"\n  - \"000651\"\n  - 600519\n",
                 encoding="utf-8")
    cfg = load_config(str(f))
    assert cfg["stock_pool"] == ["000333", "000651", "600519"]


def test_agent_volume_aliases_and_token_budget(monkeypatch, tmp_path):
    """shares/quantity 别名照收；max_tokens 透传给 provider（推理模型留足答案区）。"""
    from paper_trading.llm import provider as prov

    seen: dict = {}
    plan = {"actions": [
        {"action": "buy", "symbol": "600519", "shares": 100, "reason": "别名1"},
        {"action": "buy", "symbol": "600519", "quantity": 100, "reason": "别名2"},
    ], "summary": "别名测试"}

    def fake(cfg, messages, system=""):
        seen["max_tokens"] = cfg.max_tokens
        seen["extra_body"] = cfg.extra_body
        return json.dumps(plan)

    monkeypatch.setattr(prov, "chat", fake)
    b = _bridge(str(tmp_path), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0,
                                  llm_max_tokens=4096, llm_thinking="disabled"))
    res = t.run(["600519"], dry_run=True)
    assert seen["max_tokens"] == 4096
    assert seen["extra_body"] == {"thinking": {"type": "disabled"}}
    assert res["ok"]
    assert all("试运行通过" in d["status"] for d in res["decisions"])


def test_agent_prompt_includes_thesis(monkeypatch, tmp_path):
    """母库论点/卖出条件必须进 prompt（打法对齐的证据）。"""
    import sqlite3

    from paper_trading.llm import provider as prov

    mdir = tmp_path / "sd"
    (mdir / "data" / "db").mkdir(parents=True)
    (mdir / "src").mkdir()
    c = sqlite3.connect(str(mdir / "data" / "db" / "stock_dashboard.db"))
    c.execute("CREATE TABLE watchlist_thesis (code TEXT, core_thesis TEXT,"
              " sell_conditions TEXT)")
    c.execute("INSERT INTO watchlist_thesis VALUES "
              "('600519','好公司','跌破MA20卖出')")
    c.commit()
    c.close()
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(mdir))
    seen: dict = {}

    def fake(cfg, messages, system=""):
        seen["prompt"] = messages[0]["content"]
        seen["system"] = system
        return json.dumps({"actions": [], "summary": "x"})

    monkeypatch.setattr(prov, "chat", fake)
    b = _bridge(str(tmp_path), _fresh_bars())
    AgentTrader(b, AgentConfig()).run(["600519"], dry_run=True)
    assert "跌破MA20卖出" in seen["prompt"]
    assert "基本面一票否决" in seen["system"]


def test_price_map_covers_positions_outside_pool(monkeypatch, tmp_path):
    """持仓不在 run 池里也要按现价计入 NAV（否则持仓归零误触发熔断）。"""
    import paper_trading.cli as hb
    from paper_trading.models import Order, OrderType

    f1 = tempfile.mktemp(suffix=".db", dir=str(tmp_path))
    f2 = tempfile.mktemp(suffix=".db", dir=str(tmp_path))
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                        secrets_path=str(tmp_path / "s.json"))
    b.data_db.upsert_bars([mkbar("600519", i, 10.0) for i in range(5)])
    b.data_db.upsert_bars([mkbar("000001", i, 20.0) for i in range(5)])
    # 持仓 600519，但 run 池里只有 000001
    b.broker.submit_order(Order(symbol="600519", direction=1, volume=100,
                                order_type=OrderType.LIMIT, limit_price=10.0))
    m = b.price_map(["000001"])
    assert m["000001"] == 20.0 and m["600519"] == 10.0  # 持仓按现价，不归零
    nav = b.broker.get_nav(m)
    assert nav.market_value == 1000.0
