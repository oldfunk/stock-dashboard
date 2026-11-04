"""T64 配置接线审计：工具本身有牙齿 + 断层修复被行为级断言钉住。

为什么不是"跑一遍审计期望它绿"就完事：绿只能说明**今天**没断层。这里同时锁两件事——
① 检测器对合成源码真的会红（否则门禁是个摆设，下一版照样漏传）；
② 改一个配置值，**请求里真的变**（这才是 `llm_timeout` 那类断层的直接反证：
   当时 `agent.llm_timeout` 改了也没用，因为问答路径压根没读它）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))   # wiring_audit 是脚本式工具，没有 tests/__init__.py

from paper_trading import cli as hb                      # noqa: E402
from paper_trading.llm import provider as prov           # noqa: E402
import wiring_audit as wa                                 # noqa: E402


# ------------------------------------------------------------------ ① 检测器有牙齿

def test_read_detection_counts_uses_not_definitions():
    """声明、构造 kwarg、字典字面量键都**不是**消费；只有真读才算。"""
    files = {
        "paper_trading/fake.py": '''
from dataclasses import dataclass


@dataclass
class FakeConfig:
    used_field: int = 1
    dead_field: int = 2
    only_built: int = 3


def build(cfg):
    return FakeConfig(used_field=cfg.used_field, only_built=1)


def consumer(cfg):
    return cfg.used_field + 1


def dict_consumer(d):
    return d.get("used_field") + d["used_field"]
''',
    }
    scan = wa.scan_sources(files)
    surfaces = {"FakeConfig": {"used_field", "dead_field", "only_built"}}
    dead = {d["field"] for d in wa.dead_knobs(scan, surfaces)}
    assert "dead_field" in dead, "定义了没人读 ⇒ 必须报零消费"
    # 只被构造 kwarg 写入、从没被读的字段同样是断层——"配了个值然后丢掉"
    assert "only_built" in dead
    assert "used_field" not in dead          # 属性读 + dict 读 + 下标读都算数


def test_kwarg_write_alone_does_not_count_as_use():
    """`Foo(x=1)` 是写；只有 `obj.x` / `d["x"]` / `.get("x")` / `getattr(o,"x")` 算用。"""
    files = {"paper_trading/fake.py": '''
def make():
    return Cfg(written_only=1)


def other(d, o):
    return d.get("read_via_get"), o.read_via_attr, d["read_via_sub"], getattr(o, "read_via_getattr")
'''}
    scan = wa.scan_sources(files)
    assert not scan.sites("written_only")          # 写点进 declares，不进 reads
    for name in ("read_via_get", "read_via_attr", "read_via_sub", "read_via_getattr"):
        assert scan.sites(name), name


def test_helper_site_hardcoding_is_caught_literal_only():
    """`_ask_llm(cfg, p, s, timeout, 1, max_tokens, thinking)` 里的裸 `1` 必须被点出来；
    薄壳委托 `chat_with_retry(timeout=timeout,…)` 不许被误报。"""
    hard = wa._hardcoded_helper_args("cfg, p, s, bridge.agent_cfg.llm_timeout, 1, mt, th")
    assert hard and "llm_retries" in hard[0]
    assert wa._hardcoded_helper_args(
        'cfg, [{"role":"user","content":prompt}], system, '
        "timeout=timeout, retries=retries, max_tokens=max_tokens, thinking=thinking") == []
    cell = wa._site_cell("llm_retries", "cfg, msgs, sys, retries=self.cfg.llm_retries")
    assert cell["ok"] is True
    assert wa._site_cell("llm_retries", "cfg, msgs, sys, retries=1")["ok"] is False


def test_exempt_entries_must_carry_a_reason():
    """豁免不给理由就算断层——防止为了让门禁变绿把问题扫到地毯下。"""
    for (surface, field), reason in wa.EXEMPT.items():
        assert str(reason).strip(), f"{surface}.{field} 的豁免没写理由"
        assert len(str(reason)) > 12, f"{surface}.{field} 的理由太短，看不出为什么"


def test_yaml_section_surfaces_are_all_resolved():
    """审计的入口面必须都存在（面写错=那一面从此没人查，比漏传更隐蔽）。"""
    for surface in wa.SURFACE_SPECS:
        assert wa.surface_fields(surface), surface
    for section, surface in wa.YAML_SECTION_SURFACE.items():
        assert surface in wa.SURFACE_SPECS, f"{section} 指向了不存在的面 {surface}"


# ------------------------------------------------------------------ ② 行为级接线断言

@pytest.fixture()
def bridge_with_llm(tmp_path, monkeypatch):
    """造一个能发请求但不发真请求的桥：secrets 里放假地址，`_request` 抓包。"""
    secrets = tmp_path / "secrets.local.json"
    secrets.write_text(json.dumps({"provider": {
        "base_url": "http://127.0.0.1:9999/v1", "api_key": "",
        "model": "测试模型"}}), encoding="utf-8")
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path=str(tmp_path / "nope.yaml"),
                         secrets_path=str(secrets))
    captured: dict = {}

    def fake_request(cfg, path, payload=None):
        captured["cfg_timeout"] = cfg.timeout
        captured["cfg_max_tokens"] = cfg.max_tokens
        captured["cfg_extra_body"] = dict(cfg.extra_body or {})
        captured["payload"] = payload or {}
        captured["calls"] = captured.get("calls", 0) + 1
        return {"choices": [{"message": {"content": "答"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(prov, "_request", fake_request)
    monkeypatch.setattr(b, "llm_context",
                        lambda *a, **kw: {"account": {"cash": 1}, "positions": [],
                                          "legend": {}})
    return b, captured


def test_qa_path_carries_timeout_and_thinking(bridge_with_llm):
    """问答路径必须吃 `agent.llm_timeout/llm_thinking`（11-03 那次只有 timeout 被补上，
    thinking 与 retries 还是 LLMConfig 自己的默认——等于面板问答从来不受配置控制）。"""
    b, cap = bridge_with_llm
    b.agent_cfg.llm_timeout = 77.0
    b.agent_cfg.llm_thinking = "disabled"
    out = b.llm_ask("现在仓位多少")
    assert out["ok"] is True, out
    assert cap["cfg_timeout"] == 77.0
    assert cap["payload"]["thinking"] == {"type": "disabled"}


def test_qa_output_budget_is_ask_specific_and_overridable(bridge_with_llm):
    """问答不继承决策的 8192：实测耗时跟着 max_tokens 上限走（11-03：8192→74.9s，
    1200→28.3s，而两次真实输出都约 650 token）。默认走 ASK_MAX_TOKENS，可按次覆盖。"""
    b, cap = bridge_with_llm
    b.agent_cfg.llm_max_tokens = 8192
    b.llm_ask("默认预算")
    assert cap["payload"]["max_tokens"] == hb.ASK_MAX_TOKENS
    b.llm_ask("这次要长一点", max_tokens=4000)
    assert cap["payload"]["max_tokens"] == 4000


def test_qa_path_honours_retries_only_on_timeout(bridge_with_llm, monkeypatch):
    """超时重试也要接进问答路径（旧代码是一次超时就失败）。"""
    b, cap = bridge_with_llm
    b.agent_cfg.llm_retries = 2
    n = {"i": 0}

    def flaky(cfg, path, payload=None):
        n["i"] += 1
        if n["i"] < 3:
            raise prov.LLMError("read timed out")
        return {"choices": [{"message": {"content": "终于答了"}}]}

    monkeypatch.setattr(prov, "_request", flaky)
    out = b.llm_ask("重试看看")
    assert out["ok"] is True and n["i"] == 3

    # 非超时错误不重试（重试只会掩盖问题）
    n["i"] = 0

    def boom(cfg, path, payload=None):
        n["i"] += 1
        raise prov.LLMError("HTTP 400 参数非法")

    monkeypatch.setattr(prov, "_request", boom)
    out2 = b.llm_ask("坏请求")
    assert out2["ok"] is False and n["i"] == 1


def test_ask_thinking_enabled_does_not_send_disable_flag(bridge_with_llm):
    b, cap = bridge_with_llm
    b.agent_cfg.llm_thinking = "enabled"
    b.llm_ask("想看看推理过程")
    assert "thinking" not in cap["payload"]


def test_ask_trail_records_request_volume_and_compression(bridge_with_llm):
    """T63 可观测：请求体字符数/token 与压缩结论必须进留痕，不看日志也能回答"压没压"。"""
    b, _cap = bridge_with_llm
    b.llm_ask("体积记了吗")
    row = b.broker.get_op_log(1)[0]
    assert row["action"] == "llm:ask"
    params = json.loads(row["params"])
    assert params["req_chars"] > 0 and params["req_tokens"] > 0
    assert params["slim"] is True


def test_ask_slim_and_symbols_reach_llm_context(bridge_with_llm, monkeypatch):
    """面板"只看我的持仓"要真能落到上下文参数上，不然就是个装饰。"""
    b, _cap = bridge_with_llm
    seen = {}

    def spy(*args, **kw):
        seen.update(kw)
        return {"account": {}, "positions": []}

    monkeypatch.setattr(b, "llm_context", spy)
    b.llm_ask("只看茅台", symbols=["600519"], slim=True)
    assert seen["symbols"] == ["600519"] and seen["closes_n"] == 1 and seen["max_ops"] == 3
    b.llm_ask("要全量", slim=False)
    assert seen["closes_n"] == 5 and seen["max_ops"] == 10


def test_design_conditions_retry_is_config_driven_not_literal():
    """T64-F2 的回归钉：这里曾把重试次数硬写成 `1`，`agent.llm_retries` 改了不生效。

    不去跑真网络（`design_conditions` 会取实时行情），而是直接钉住审计在这一跳的结论：
    调用点必须引用配置字段，且没有字面量覆盖。这正是门禁会红的两种形状。
    """
    r = wa.audit()
    site = [h for h in r["llm_hop_matrix"]
            if h["function"] == "design_conditions" and h["role"] == "helper_site"]
    assert site, "审计没照到 design_conditions 这一跳（工具或调用点改名字了）"
    assert site[0]["hardcoded"] == [], site[0]["hardcoded"]
    assert site[0]["cells"]["llm_retries"]["ok"] is True, site[0]["cells"]
    assert not [h for h in r["llm_hops_unsupplied"]
                if h["function"] == "design_conditions"], r["llm_hops_unsupplied"]


def test_risk_control_block_reaches_the_context(tmp_path):
    """熔断态进上下文（T63 派发点）：零成交时模型要能看见"是被风控拦的"。"""
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path=str(tmp_path / "nope.yaml"),
                         secrets_path=str(tmp_path / "s.json"))
    b.agent_cfg.__dict__["news_enabled"] = False
    ctx = b.llm_context(max_ops=1, max_nav=1, closes_n=1, symbols=["600519"])
    rc = ctx.get("risk_control")
    assert rc and set(rc) >= {"drawdown_halt", "daily_loss_halt", "max_drawdown_pct",
                              "peak_total_value"}


def test_describe_is_read_only_and_does_not_anchor_baseline(tmp_path):
    """`describe()` 不许顺手写日亏基线：锚定是结算路径的动作，问答看一眼不能改账。"""
    import sqlite3

    from paper_trading.models import TradingConfig
    from paper_trading.risk.risk_manager import RiskManager

    db = tmp_path / "acct.db"
    RiskManager(db_path=str(db))                  # 建表
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"), account_db=str(db),
                         config_path=str(tmp_path / "nope.yaml"),
                         secrets_path=str(tmp_path / "s.json"))
    out = b.risk.describe(100_000.0, day="2026-11-03")
    with sqlite3.connect(str(db)) as c:
        rows = c.execute("SELECT key FROM risk_state").fetchall()
    assert rows == [], f"只读方法写了 risk_state：{rows}"
    assert out["daily_baseline"] is None and out["daily_loss_pct"] is None
    assert out["drawdown_halt"] is False          # 峰值为 0 时不判回撤


def test_audit_gate_passes_on_the_repo():
    """门禁本体：仓库现状必须无未豁免断层，退出码 0（CI 就按这个口径）。"""
    r = wa.audit()
    assert not r["dead_knobs_unexempted"], r["dead_knobs_unexempted"]
    assert not r["llm_hops_unsupplied"], r["llm_hops_unsupplied"]
    assert not r["yaml_mismatches"], r["yaml_mismatches"]
    assert r["ok"] is True
    assert wa.main(["--quiet"]) == 0
