"""T68：审计的间接层判定（推导版）+ `chat_with_retry` 与旧决策路径的行为等价性。

对应批复第 6 件的三个切入点与第 5 件的判定复核：
① 间接层不能靠人工名单——名单会过期，新层没登记时矩阵会把那层内部调用报成漏传、
   又把它外面的调用点整条跳过，**断层反而被洗白**；
② 但也不能"谁调了层就算层"——那样 `run/tick/main` 全变层，供给点一个都不检查；
   只有**把自己形参原样转传下去的薄壳**才算传递层；
③ `chat_with_retry` 从 `_ask_llm` 提出来之后，决策路径的语义必须一字不变：
   只重试超时、非超时错一次即抛、1210（不支持关思考）降 low 档重试并把预算抬到 ≥4096。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import wiring_audit as wa                                   # noqa: E402
from paper_trading.llm import provider as prov              # noqa: E402


# ---------------------------------------------------- ①② 间接层是推导出来的

def test_repo_helpers_are_derived_and_match_the_registry():
    r = wa.audit()
    assert sorted(r["helpers_derived"]) == sorted(wa.LLM_HELPER_EXPECT), r["helpers_derived"]
    assert r["helper_drift"] == {"unregistered": [], "obsolete": []}
    assert r["ok"] is True


def test_two_level_chain_is_followed_and_site_is_checked():
    """合成三层链：`chat` ← 写 cfg 的壳 ← 转传自己形参的壳 ← 业务函数硬编码 retries。

    推导必须把两层壳都认出来，并且**在最外面的业务调用点上**报出字面量覆盖——
    只认第一层时，这条链上的漏传会整段隐身。
    """
    files = {"paper_trading/syn.py": '''
from llm import chat


def level_one(cfg, prompt, system, timeout, retries, max_tokens, thinking):
    cfg.timeout = timeout
    cfg.max_tokens = max_tokens

    def _go(extra):
        cfg.extra_body = extra
        return chat(cfg, [{"role": "user", "content": prompt}], system)
    return _go({})


def level_two(cfg, prompt, system, timeout, retries, max_tokens, thinking):
    return level_one(cfg, prompt, system, timeout=timeout, retries=retries,
                     max_tokens=max_tokens, thinking=thinking)


def business(cfg, llm_cfg, self):
    return level_two(llm_cfg, "p", "s", self.cfg.llm_timeout, 7,
                     self.cfg.llm_max_tokens, self.cfg.llm_thinking)
'''}
    scan = wa.scan_sources(files)
    helpers = wa.detect_helpers(scan)
    assert {"level_one", "level_two"} <= set(helpers), helpers
    assert "business" not in helpers            # 业务函数是供给点，不是层
    rows = wa.llm_hop_matrix(scan, set(), helpers)
    site = [r for r in rows if r["function"] == "business"]
    assert site, rows
    assert any("llm_retries" in h for h in site[0]["hardcoded"]), site[0]["hardcoded"]
    assert site[0]["cells"]["llm_retries"]["ok"] is False


def test_caller_that_only_passes_config_is_not_promoted_to_a_layer():
    """防雪崩（我实测踩过）：`run()` 调 `_ask_llm(self.cfg.llm_timeout, …)` 是**供给点**，
    把它也算成层的话，矩阵里就没有调用点可查了，硬编码反而看不见。"""
    files = {"paper_trading/syn2.py": '''
def shell(cfg, prompt, system, timeout, retries, max_tokens, thinking):
    cfg.timeout = timeout
    return chat(cfg, [], system)


def trader(self, chat):
    return shell(self.cfg, "p", "s", self.cfg.llm_timeout, self.cfg.llm_retries,
                 self.cfg.llm_max_tokens, self.cfg.llm_thinking)
'''}
    scan = wa.scan_sources(files)
    helpers = wa.detect_helpers(scan)
    assert set(helpers) == {"shell"}, helpers
    assert "trader" not in helpers


def test_same_named_functions_in_different_files_are_both_checked():
    """同名函数不许互相遮蔽（我自己踩过：`run` 在 cli.py 与 agent/loop.py 各有一个，
    函数表只按名字建键 ⇒ 后一个的函数体永远查不到，那一行调用点**静默消失**。
    少一行比错一行更糟——没人会去查没出现的东西。"""
    files = {
        "paper_trading/shell.py": '''
def shell(cfg, prompt, system, timeout, retries, max_tokens, thinking):
    cfg.timeout = timeout
    cfg.max_tokens = max_tokens
    cfg.extra_body = {}
    return chat(cfg, [], system)
''',
        "paper_trading/good.py": '''
def run(self, cfg):
    return shell(cfg, "p", "s", self.cfg.llm_timeout, self.cfg.llm_retries,
                 self.cfg.llm_max_tokens, self.cfg.llm_thinking)
''',
        "paper_trading/bad.py": '''
def run(self, cfg):
    return shell(cfg, "p", "s", 30.0, 1, 8192, "disabled")
''',
    }
    scan = wa.scan_sources(files)
    helpers = wa.detect_helpers(scan)
    assert set(helpers) == {"shell"}, helpers
    rows = [r for r in wa.llm_hop_matrix(scan, set(), helpers)
            if r["role"] == "helper_site"]
    assert {r["file"] for r in rows} == {"paper_trading/good.py",
                                         "paper_trading/bad.py"}, rows
    bad = [r for r in rows if r["file"].endswith("bad.py")][0]
    assert bad["hardcoded"], bad["cells"]          # 全字面量那一行必须被点出来
    good = [r for r in rows if r["file"].endswith("good.py")][0]
    assert not good["hardcoded"] and all(c["ok"] for c in good["cells"].values())


def test_alias_imported_callee_is_resolved():
    """`from p import chat_with_retry as _ask` 之后调用名变了，解析必须还原成原名，
    否则那一跳整个从矩阵里消失（`llm_ask` 现在就是这么写的）。"""
    files = {"paper_trading/syn3.py": '''
from paper_trading.llm.provider import chat_with_retry as _ask
from paper_trading.shell import shell as sh


def qa(cfg, self):
    return sh(cfg, "p", "s", self.cfg.llm_timeout, self.cfg.llm_retries,
              self.cfg.llm_max_tokens, self.cfg.llm_thinking)


def other(cfg, self):
    return _ask(cfg, [], "sys", timeout=self.cfg.llm_timeout,
                retries=self.cfg.llm_retries, max_tokens=self.cfg.llm_max_tokens,
                thinking=self.cfg.llm_thinking)
'''}
    scan = wa.scan_sources(files)
    assert scan.aliases["paper_trading/syn3.py"]["sh"] == "shell"
    assert scan.aliases["paper_trading/syn3.py"]["_ask"] == "chat_with_retry"
    assert {s[1] for s in wa.iter_calls(scan, ["shell"])} == {"qa"}
    assert {s[1] for s in wa.iter_calls(scan, ["chat_with_retry"])} == {"other"}


# ---------------------------------------------------- ③ chat_with_retry 等价性

def _cfg():
    return prov.LLMConfig(base_url="http://x/v1", api_key="", model="m")


def test_timeout_is_retried_exactly_retries_times(monkeypatch):
    """旧 `_ask_llm` 的口径：只重试超时，总尝试次数 = retries + 1。"""
    n = {"i": 0}

    def flaky(cfg, messages, system=""):
        n["i"] += 1
        raise prov.LLMError("The read operation timed out")

    monkeypatch.setattr(prov, "chat", flaky)
    with pytest.raises(prov.LLMError):
        prov.chat_with_retry(_cfg(), [{"role": "user", "content": "q"}], "s",
                             timeout=10.0, retries=2)
    assert n["i"] == 3


def test_non_timeout_error_is_not_retried(monkeypatch):
    n = {"i": 0}

    def boom(cfg, messages, system=""):
        n["i"] += 1
        raise prov.LLMError("HTTP 400 参数非法")

    monkeypatch.setattr(prov, "chat", boom)
    with pytest.raises(prov.LLMError):
        prov.chat_with_retry(_cfg(), [{"role": "user", "content": "q"}], "s",
                             timeout=10.0, retries=3)
    assert n["i"] == 1


def test_thinking_unsupported_falls_back_to_low_and_bigger_budget(monkeypatch):
    """1210（这模型始终思考）：第二次要带 `thinking enabled + level low`，
    并把输出预算抬到至少 4096——旧实现就是这样，收敛后不许丢。"""
    calls = []

    def fake_chat(cfg, messages, system=""):
        calls.append({"extra": dict(cfg.extra_body or {}), "max_tokens": cfg.max_tokens})
        if len(calls) == 1:
            raise prov.LLMError("HTTP 400 /chat/completions：1210 不支持关闭思考")
        return "答"

    monkeypatch.setattr(prov, "chat", fake_chat)
    out = prov.chat_with_retry(_cfg(), [{"role": "user", "content": "q"}], "s",
                               timeout=5.0, retries=0, max_tokens=2048,
                               thinking="disabled")
    assert out == "答" and len(calls) == 2
    assert calls[0]["extra"] == {"thinking": {"type": "disabled"}}
    assert calls[1]["extra"] == {"thinking": {"type": "enabled", "level": "low"}}
    assert calls[0]["max_tokens"] == 2048
    assert calls[1]["max_tokens"] >= 4096          # 预算抬起来，不然只剩思考没答案


def test_ask_llm_shell_keeps_the_old_positional_signature(monkeypatch):
    """决策路径的老调用点全是位置参数：壳的签名与转传必须逐位对应，不能改语义。"""
    seen = {}

    def fake_chat(cfg, messages, system=""):
        seen["timeout"] = cfg.timeout
        seen["max_tokens"] = cfg.max_tokens
        seen["extra"] = dict(cfg.extra_body or {})
        seen["messages"] = messages
        seen["system"] = system
        return '{"buy": []}'

    monkeypatch.setattr(prov, "chat", fake_chat)
    from paper_trading.agent.loop import _ask_llm

    cfg = _cfg()
    out = _ask_llm(cfg, "问题", "系统", 33.0, 1, 6000, "disabled")
    assert out == '{"buy": []}'
    assert json.loads(out) == {"buy": []}                 # 返回物仍是可解析的字符串
    assert isinstance(out, str)
    assert seen["timeout"] == 33.0 and seen["max_tokens"] == 6000
    assert seen["extra"] == {"thinking": {"type": "disabled"}}
    assert seen["messages"] == [{"role": "user", "content": "问题"}]


def test_chat_retries_do_not_mutate_the_caller_budget_between_attempts(monkeypatch):
    """重试期间 cfg 被反复写 `extra_body` 是旧行为；唯一不许变的是"预算只抬高不压低"——
    降下来会让第二次尝试比第一次更容易截断（静默截断正是 T67 要消灭的）。"""
    budgets = []

    def flaky(cfg, messages, system=""):
        budgets.append(cfg.max_tokens)
        if len(budgets) < 3:
            raise prov.LLMError("read timed out")
        return "ok"

    monkeypatch.setattr(prov, "chat", flaky)
    prov.chat_with_retry(_cfg(), [{"role": "user", "content": "q"}], "s",
                         timeout=1.0, retries=2, max_tokens=1536, thinking="enabled")
    assert budgets == [1536, 1536, 1536]
