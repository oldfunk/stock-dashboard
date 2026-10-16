"""AI 自主提名（pass1）单测：解析、代码校验、并池、fail-closed，全离线。"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent import AgentConfig, AgentTrader  # noqa: E402
from paper_trading.agent.loop import parse_picks  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


@pytest.fixture(autouse=True)
def _no_net(monkeypatch):
    from paper_trading.cli import TradingBridge
    monkeypatch.setattr(TradingBridge, "sync_data",
                        lambda self, symbols: {"ok": True, "symbols": symbols,
                                               "updated": {s: 1 for s in symbols}})


NAMES = {"600519": "贵州茅台", "000858": "五粮液"}


def _bridge(tmp, syms=("600519",), fresh=True):
    import tempfile

    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                         secrets_path=str(Path(tmp) / "s.json"))
    day = datetime.now()
    if fresh:
        for s in syms:
            b.data_db.upsert_bars([
                Bar(symbol=s, timestamp=day + timedelta(days=10000 + i), open=10.0,
                    high=11.0, low=9.0, close=10.0, volume=100) for i in range(30)])
    b.data_db.upsert_stock_names({s: NAMES[s] for s in syms if s in NAMES})
    b.llm_save("custom", "https://x/v1", "m", "k")
    return b


def test_parse_picks_shapes():
    assert parse_picks('{"picks":[],"summary":"无"}') == []
    assert parse_picks('{"picks":[{"query":"贵州茅台","reason":"低估"}]}') == \
        [{"query": "贵州茅台", "reason": "低估"}]
    # 字符串条目也认（跨模型容错）
    assert parse_picks('{"picks":["600519"]}') == [{"query": "600519", "reason": ""}]
    # 名字字段别名
    assert parse_picks('{"picks":[{"name":"平安"}]}')[0]["query"] == "平安"
    # 上限截断
    many = json.dumps({"picks": [{"query": str(i)} for i in range(20)]})
    assert len(parse_picks(many, 5)) == 5
    # 脏输出 fail-closed
    for bad in [None, "", "没有 JSON", '{"picks": "x"}', "[1,2]", '{"picks":[null,3]}']:
        assert parse_picks(bad) == []


def test_enrich_merges_valid_picks(monkeypatch, tmp_path):
    """AI 提名全市场个股 → 程序校验 → 并入本轮范围。"""
    from paper_trading.llm import provider as prov

    b = _bridge(str(tmp_path), syms=("600519", "000858"))
    b.llm_save("custom", "https://x/v1", "m", "k")
    calls = {"n": 0}

    def fake_chat(cfg, messages, system=""):
        calls["n"] += 1
        if "全市场选股助手" in system:      # pass1
            return json.dumps({"picks": [{"query": "五粮液", "reason": "低估"},
                                         {"query": "600519", "reason": "已在池内"}],
                               "summary": "提名一只"})
        return json.dumps({"actions": [], "summary": "不动"})   # pass2

    monkeypatch.setattr(prov, "chat", fake_chat)
    t = AgentTrader(b, AgentConfig(picks_enabled=True, picks_max=5))
    merged, picks = t._enrich(["600519"], [{"symbol": "600519", "name": "贵州茅台",
                                             "score": 95, "pe": 20, "reason": "好"}])
    # 提名里 600519 已在池内被过滤，只剩五粮液；母库缺席时靠本地名称表兜底匹配
    assert merged == ["600519", "000858"]
    assert picks and picks[0]["symbol"] == "000858"
    assert picks[0]["reason"] == "低估"
    assert calls["n"] == 1          # 每轮一次提名调用


def test_picks_disabled(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    b = _bridge(str(tmp_path))
    monkeypatch.setattr(prov, "chat", lambda *a, **k: pytest.fail("禁用时不应调 LLM"))
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    assert t._enrich(["600519"], [{"symbol": "600519", "name": "x", "score": 1,
                                   "pe": 1, "reason": ""}]) == (["600519"], [])


def test_pick_bad_json_does_not_block(monkeypatch, tmp_path):
    from paper_trading.llm import provider as prov

    b = _bridge(str(tmp_path))
    monkeypatch.setattr(prov, "chat", lambda cfg, m, system="": "完全不是 JSON")
    t = AgentTrader(b, AgentConfig())
    assert t._enrich(["600519"], []) == (["600519"], [])


def test_run_decides_on_picked_universe(monkeypatch, tmp_path):
    """提名并入后，AI 对提名股票的动作被放行（池外→池内），仍受风控约束。"""
    from paper_trading.llm import provider as prov

    b = _bridge(str(tmp_path), syms=("600519", "000858"))
    seen = {}

    def fake_chat(cfg, messages, system=""):
        if "全市场选股助手" in system:
            return json.dumps({"picks": [{"query": "五粮液", "reason": "低估"}],
                               "summary": ""})
        seen["prompt"] = messages[-1]["content"]
        seen["system"] = system
        return json.dumps({"actions": [{"action": "buy", "symbol": "000858",
                                        "volume": 100, "reason": "提名买入"}],
                           "summary": "买提名股"})

    monkeypatch.setattr(prov, "chat", fake_chat)
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0))
    res = t.run(["600519"], dry_run=True, ignore_schedule=True)
    assert res["ok"] and res["picks"] and res["picks"][0]["symbol"] == "000858"
    assert "000858" in res["symbols"]
    assert "五粮液(000858)" in seen["prompt"]        # 提名进决策上下文
    assert "AI 自主提名标的" in seen["system"]   # 提名段进 system（代码已校验）
    d = res["decisions"][0]
    assert "试运行通过" in d["status"]                # 提名股未被"不在池"拒绝
    ops = b.broker.get_op_log(5)
    ai = next(o for o in ops if o["action"] == "ai:decide" and "decisions" in (o["result"] or ""))
    assert '"picks"' in ai["result"] and "000858" in ai["result"]   # 提名入流水可审计
    assert ai["params"] and '"n_picks": 1' in ai["params"]


def test_no_picks_call_when_stale_bars(monkeypatch, tmp_path):
    """新鲜守卫拦下时不做提名（省一次 LLM 与补数）。"""
    from paper_trading.agent.loop import AgentTrader as _AT
    from paper_trading.llm import provider as prov
    from paper_trading.models import Bar as _Bar

    b = _bridge(str(tmp_path), fresh=False)
    # 只放过期 bars（2024 年），触发 no-fresh-bars
    b.data_db.upsert_bars([_Bar(symbol="600519", timestamp=datetime(2024, 1, 1),
                                open=10.0, high=11.0, low=9.0, close=10.0,
                                volume=100)])
    monkeypatch.setattr(prov, "chat",
                        lambda *a, **k: pytest.fail("守卫拦下时不应调 LLM"))
    res = _AT(b, AgentConfig()).run(["600519"], dry_run=True, ignore_schedule=True)
    assert res["skipped"] == "no-fresh-bars"