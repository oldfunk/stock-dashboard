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
from paper_trading.models import Bar  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """禁网铁律：单测永不碰真实行情源（有网环境会拉回真数据污染断言）。"""
    from paper_trading.cli import TradingBridge

    monkeypatch.setattr(
        TradingBridge, "sync_data",
        lambda self, symbols: {"ok": True, "symbols": symbols, "updated": {}})


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
    pend = b.broker.get_pending_plan(
        __import__("datetime").date.today().isoformat())
    assert pend and pend["status"] == "pending"
    r2 = t.run(["600519"])  # 开盘执行（消费计划，不再问 LLM）
    assert r2["ok"] and r2.get("from_plan") is True
    assert r2["decisions"][0]["status"].startswith("已成交")
    assert b.broker.get_position("600519").total_volume == 100
    assert b.broker.get_pending_plan(
        __import__("datetime").date.today().isoformat()) is None  # 已消费
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
