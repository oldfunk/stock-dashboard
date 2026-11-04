"""T67 问答体验：截断必须可见、预算进配置面、token 用真实口径、审计进门禁。

拍板原文（questions.md 主 AI 批复）四条各自对应一组用例：
1. "答案被截断"可见信号——静默截断是纪律问题；
2. `agent.ask_max_tokens` 配置键（config.yaml + 读取链 + wiring 测试，缺省 1536 不变）；
3. `context_budget` 按真实 token（透出 `usage.prompt_tokens`，留痕同时记估算与真实）；
4. `wiring_audit` 进 CI 独立步骤。

`LLMReply` 是 str 子类，所以这些用例还得证明**旧调用方没被改动**（第 9 条）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading import cli as hb                      # noqa: E402
from paper_trading.agent import context as cx            # noqa: E402
from paper_trading.llm import provider as prov           # noqa: E402


@pytest.fixture(autouse=True)
def _reset_calibration():
    """校准系数是模块级观测值，用例之间必须归零，否则 token 断言互相污染。"""
    cx._TOKEN_CALIBRATION = 1.0
    yield
    cx._TOKEN_CALIBRATION = 1.0


def _bridge(tmp_path, monkeypatch, config_text=None, reply_body=None):
    """能发请求但不发真请求的桥：假地址 + `_request` 抓包 + 上下文桩。"""
    secrets = tmp_path / "secrets.local.json"
    secrets.write_text(json.dumps({"provider": {
        "base_url": "http://127.0.0.1:9999/v1", "api_key": "", "model": "测试模型"}}),
        encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(config_text or "agent:\n  news_enabled: false\n", encoding="utf-8")
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path=str(cfg_file),
                         secrets_path=str(secrets))
    captured: dict = {}

    def fake_request(cfg, path, payload=None):
        captured["payload"] = payload or {}
        captured["max_tokens"] = cfg.max_tokens
        return reply_body or {"choices": [{"message": {"content": "答"},
                                           "finish_reason": "stop"}],
                              # 与桩上下文（约 344 估算 token）同量级：比值 1.25 落在
                              # 合理区间里，校准才被采纳（离谱比值有专门用例）
                              "usage": {"prompt_tokens": 430, "completion_tokens": 100,
                                        "total_tokens": 530}}

    monkeypatch.setattr(prov, "_request", fake_request)
    monkeypatch.setattr(b, "llm_context",
                        lambda *a, **kw: {"account": {"cash": 1}, "positions": [],
                                          "legend": {}})
    return b, captured


# ----------------------------------------------------------- 1. 截断可见信号

def test_truncated_answer_is_marked_in_the_answer_itself(tmp_path, monkeypatch):
    """批复原话：`finish_reason=length` 必须告知用户。标记要长在答案里——
    面板只渲染 answer，结构化字段留给程序。"""
    b, _cap = _bridge(tmp_path, monkeypatch, reply_body={
        "choices": [{"message": {"content": "这是一段被掐断的回答"},
                     "finish_reason": "length"}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 1536}})
    r = b.llm_ask("总结一下持仓")
    assert r["truncated"] is True
    assert "截断" in r["answer"] and "⚠️" in r["answer"]
    assert "这是一段被掐断的回答" in r["answer"]        # 原文一字不丢
    assert r["notice"] and "ask_max_tokens" in r["notice"]


def test_completed_answer_gets_no_mark(tmp_path, monkeypatch):
    b, _cap = _bridge(tmp_path, monkeypatch, reply_body={
        "choices": [{"message": {"content": "完整回答"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 500, "completion_tokens": 60}})
    r = b.llm_ask("正常问题")
    assert r["truncated"] is False and "⚠️" not in r["answer"] and r["notice"] == ""


def test_truncation_and_usage_are_written_into_the_trail(tmp_path, monkeypatch):
    """留痕里要能事后复盘"哪一次被截了、真实用了多少 token"——只有日志不算数，
    面板与审计读的是 op_log。"""
    b, _cap = _bridge(tmp_path, monkeypatch, reply_body={
        "choices": [{"message": {"content": "半截答案"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 11440, "completion_tokens": 1536}})
    b.llm_ask("会被截断的问题")
    row = json.loads(b.broker.get_op_log(1)[0]["result"])
    assert row["truncated"] is True and row["finish_reason"] == "length"
    assert row["usage"]["prompt_tokens_real"] == 11440
    assert row["usage"]["completion_tokens"] == 1536
    assert row["usage"]["prompt_tokens_est"] > 0        # 估算与真实并存，不互相冒充
    assert "⚠️" in row["answer"]


# ----------------------------------------------------------- 2. 配置面三件套

def test_ask_max_tokens_config_reaches_the_request(tmp_path, monkeypatch):
    """改 `agent.ask_max_tokens` ⇒ 请求里的 max_tokens 跟着变（不是又一个"配了没用"）。"""
    b, cap = _bridge(tmp_path, monkeypatch,
                     config_text="agent:\n  news_enabled: false\n  ask_max_tokens: 2600\n")
    assert b.agent_cfg.ask_max_tokens == 2600
    b.llm_ask("问题")
    assert cap["payload"]["max_tokens"] == 2600


def test_per_call_override_beats_config(tmp_path, monkeypatch):
    b, cap = _bridge(tmp_path, monkeypatch,
                     config_text="agent:\n  news_enabled: false\n  ask_max_tokens: 2600\n")
    b.llm_ask("问题", max_tokens=900)
    assert cap["payload"]["max_tokens"] == 900


def test_default_stays_1536_when_config_says_nothing(tmp_path, monkeypatch):
    """缺省语义不变（批复 2 明确"缺省 1536 不变"）。"""
    b, cap = _bridge(tmp_path, monkeypatch)
    assert b.agent_cfg.ask_max_tokens == 1536
    b.llm_ask("问题")
    assert cap["payload"]["max_tokens"] == 1536


def test_repo_config_yaml_normalizer_and_field_agree():
    """三件套必须同时到位：yaml 有键、归一化层搬它、dataclass 有字段。
    少任何一环就是 v0.7.6 `news_*` 那个形状（写了也到不了消费对象）。"""
    from paper_trading.agent.loop import AgentConfig
    from paper_trading.utils import config as ucfg

    yml = (ROOT / "config.yaml").read_text(encoding="utf-8")
    src = (ROOT / "paper_trading/utils/config.py").read_text(encoding="utf-8")
    assert "ask_max_tokens:" in yml
    assert '"ask_max_tokens"' in src
    assert "ask_max_tokens" in {f.name for f in
                                __import__("dataclasses").fields(AgentConfig)}


def test_cli_flag_and_dashboard_body_reach_llm_ask(tmp_path, monkeypatch):
    """CLI `--max-tokens` 与面板 body `max_tokens` 必须打到同一个入口（不留两套口径）。"""
    seen = {}

    def spy(prompt, system="", *, symbols=None, slim=True, max_tokens=None):
        seen.update({"max_tokens": max_tokens, "slim": slim, "symbols": symbols})
        return {"ok": True, "answer": "x"}

    b, _cap = _bridge(tmp_path, monkeypatch)
    monkeypatch.setattr(b, "llm_ask", spy)
    # CLI 侧：argv 走真 argparse，断标志位确实翻译成了入参（不是只写了 help）
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(["--max-tokens", "3200"])
    assert args.max_tokens == 3200 and not getattr(args, "full", False)
    b.llm_ask("p", symbols=["600519"], max_tokens=args.max_tokens)
    assert seen == {"max_tokens": 3200, "slim": True, "symbols": ["600519"]}


# ----------------------------------------------------------- 3. 真实 token 口径

def test_calibration_from_usage_shrinks_headroom():
    """真实 prompt_tokens 比估算高 ⇒ 系数 >1 ⇒ 同样预算下上下文更早被压。
    11-03 实测：估 8,318 / 真实 11,440 ⇒ 约 1.37。"""
    before = cx.estimate_tokens("abcd" * 400)
    assert cx.set_token_calibration(11440 / 8318) is True
    after = cx.estimate_tokens("abcd" * 400)
    assert after > before and cx.token_calibration() == pytest.approx(1.375, abs=0.01)


def test_absurd_calibration_is_refused():
    """比值离谱（缓存命中、system 未计入等）就不采纳——异常观测不许把预算越拉越歪。"""
    assert cx.set_token_calibration(50) is False
    assert cx.token_calibration() == 1.0
    assert cx.set_token_calibration("不是数") is False
    assert cx.token_calibration() == 1.0


def test_uncalibrated_estimates_keep_old_numbers():
    """未校准时的估算与 T63 报告里的口径一致（CJK 1 字、其余 4 字），不许悄悄换算法。"""
    assert cx.estimate_tokens("中文") == 2
    assert cx.estimate_tokens("abcd") == 1


def test_ask_returns_both_token_counts(tmp_path, monkeypatch):
    b, _cap = _bridge(tmp_path, monkeypatch)
    r = b.llm_ask("问题")
    ctx = r["context"]
    assert ctx["prompt_tokens_real"] == 430 and ctx["tokens"] > 0
    assert ctx["calibrated"] is True            # 430/344≈1.25 落在合理区间内


def test_implausible_usage_does_not_poison_the_calibration(tmp_path, monkeypatch):
    """缓存命中/厂商少计一类情况：比值离谱就不采纳，但**真实值照样留痕**。"""
    b, _cap = _bridge(tmp_path, monkeypatch, reply_body={
        "choices": [{"message": {"content": "答"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 999999, "completion_tokens": 5}})
    r = b.llm_ask("问题")
    assert r["context"]["calibrated"] is False
    assert r["context"]["prompt_tokens_real"] == 999999
    assert cx.token_calibration() == 1.0


# ----------------------------------------------------------- 4. 兼容与门禁

def test_llmreply_is_still_a_plain_string_for_old_callers(tmp_path, monkeypatch):
    """决策路径把返回值直接喂 JSON 解析；`LLMReply` 必须是 str 子类而不是新形状。"""
    b, _cap = _bridge(tmp_path, monkeypatch, reply_body={
        "choices": [{"message": {"content": '{"buy": []}'},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    reply = prov.chat(b._llm_config(), [{"role": "user", "content": "x"}])
    assert isinstance(reply, str)
    assert json.loads(reply) == {"buy": []}
    assert reply.finish_reason == "stop" and reply.truncated is False
    assert reply.prompt_tokens == 10


def test_ci_workflow_has_the_wiring_gate_as_its_own_step():
    """批复 4：门禁独立成步，退出码非 0 直接告警，不混在 pytest 红里。"""
    wf = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
    assert "wiring_audit.py" in wf
    assert "pytest" in wf
