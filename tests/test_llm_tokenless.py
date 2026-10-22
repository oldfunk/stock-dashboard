"""本地/局域网接口免令牌支持（真机事故回归）。

背景：
1. 面板原来**强制要 Key** 才能保存/拉模型，但本地推理服务（Ollama、LM Studio、
   vLLM、自建网关）根本不校验令牌 —— 用户拿它们没法用。
2. 空 Key 时还发 `Authorization: Bearer `（空令牌），有些服务直接 401。
3. 「AI 设计条件单」一直 500：规则正文里的 JSON 示例被 `str.format` 当占位符，
   `KeyError: '"conditions"'`（面板只显示"失败"，看起来像 API 挂了）。
4. 推理模型把输出预算全花在 thinking 上时 content 为空，原样返回空串，
   用户看到"空回答"却不知道原因。
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.llm import LLMConfig, LLMError, chat, list_models  # noqa: E402


class _NoTokenAPI(BaseHTTPRequestHandler):
    """模拟本地服务：不校验令牌，但**记录**收到的 Authorization 头。"""

    seen_auth: list = []

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/v1/models":
            type(self).seen_auth.append(self.headers.get("Authorization"))
            return self._send(200, {"data": [{"id": "qwen3:8b"}, {"id": "llama3.2"}]})
        return self._send(404, {})

    def do_POST(self):  # noqa: N802
        if self.path == "/v1/chat/completions":
            type(self).seen_auth.append(self.headers.get("Authorization"))
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if body["model"] == "thinking-only":
                # 推理模型把预算花光：只有思考、没有答案
                return self._send(200, {"choices": [{
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning_content": "让我想想…"}}]})
            if body["model"] == "empty":
                return self._send(200, {"choices": [{
                    "finish_reason": "stop", "message": {"content": ""}}]})
            return self._send(200, {"choices": [{
                "finish_reason": "stop",
                "message": {"content": "本地模型回答"}}]})
        return self._send(404, {})

    def log_message(self, *a):
        pass


def _srv():
    _NoTokenAPI.seen_auth = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _NoTokenAPI)
    Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _cfg(srv, **kw):
    args = {"base_url": f"http://127.0.0.1:{srv.server_port}/v1",
            "api_key": "", "model": "qwen3:8b"}
    args.update(kw)
    return LLMConfig(**args)


def test_no_token_fetch_and_chat():
    """只写接口地址、不写令牌：能拉模型、能对话，且**不发** Authorization 头。"""
    srv = _srv()
    try:
        assert list_models(_cfg(srv)) == ["qwen3:8b", "llama3.2"]
        assert chat(_cfg(srv), [{"role": "user", "content": "你好"}]) == "本地模型回答"
        assert _NoTokenAPI.seen_auth == [None, None]  # 两次都没有 Authorization
    finally:
        srv.shutdown()


def test_token_still_sent_when_present():
    """有 Key 时照旧发送（不破坏云端厂商的行为）。"""
    srv = _srv()
    try:
        list_models(_cfg(srv, api_key="sk-x"))
        assert _NoTokenAPI.seen_auth == ["Bearer sk-x"]
    finally:
        srv.shutdown()


def test_empty_answer_raises_clear_error():
    """答案为空要看得出原因（thinking 吃光预算 vs 真的空返回）。"""
    srv = _srv()
    try:
        try:
            chat(_cfg(srv, model="thinking-only"), [{"role": "user", "content": "x"}])
            raise AssertionError("should raise")
        except LLMError as e:
            assert "思考" in str(e) and "length" in str(e)
        try:
            chat(_cfg(srv, model="empty"), [{"role": "user", "content": "x"}])
            raise AssertionError("should raise")
        except LLMError as e:
            assert "空答案" in str(e)
    finally:
        srv.shutdown()


def test_always_thinking_model_falls_back_to_low(monkeypatch):
    """GLM 5.3 系列"始终思考"，拒绝 disabled（400/1210）：自动降到 low 档重试一次。"""
    from paper_trading.agent.loop import _ask_llm
    from paper_trading.llm import LLMConfig, LLMError
    from paper_trading.llm import provider as prov

    seen = []

    def fake_chat(cfg, messages, system=""):
        seen.append((dict(cfg.extra_body), cfg.max_tokens))
        if cfg.extra_body.get("thinking", {}).get("type") == "disabled":
            raise LLMError('HTTP 400 /chat/completions：{"error":{"code":"1210",'
                           '"message":"该模型始终思考，不支持关闭思考；请使用 low、high 或 max。"}}')
        return "答"

    monkeypatch.setattr(prov, "chat", fake_chat)
    cfg = LLMConfig(base_url="http://x/v1", api_key="k", model="glm-5.3-flash")
    assert _ask_llm(cfg, "x", "", 10, 0, max_tokens=2048) == "答"
    assert seen[0] == ({"thinking": {"type": "disabled"}}, 2048)
    # 思考也占输出预算：降档同时把预算抬到至少 4096
    assert seen[1] == ({"thinking": {"type": "enabled", "level": "low"}}, 4096)


def test_other_llm_errors_are_not_retried(monkeypatch):
    from paper_trading.agent.loop import _ask_llm
    from paper_trading.llm import LLMConfig, LLMError
    from paper_trading.llm import provider as prov

    calls = []

    def fake_chat(cfg, messages, system=""):
        calls.append(1)
        raise LLMError("HTTP 401 bad key")

    monkeypatch.setattr(prov, "chat", fake_chat)
    cfg = LLMConfig(base_url="http://x/v1", api_key="k", model="m")
    try:
        _ask_llm(cfg, "x", "", 10, 0)
        raise AssertionError("should raise")
    except LLMError as e:
        assert "401" in str(e) and len(calls) == 1


def test_build_prompt_does_not_crash_on_json_braces():
    """回归：「AI 设计条件单」500 的根因——规则正文含 JSON 大括号，不能走 str.format。"""
    from paper_trading.engine.ai_conditions import build_prompt

    sys_p, user_p = build_prompt(["600036"], {"600036": {"price": 40.0,
                                                         "change_pct": 1.2}},
                                 [], "方案：通用", "2026-10-20",
                                 max_conds=3, max_days=2)
    assert '"conditions"' in sys_p          # JSON 示例原样保留
    assert "最多 3 条" in sys_p             # 占位符确实被替换
    assert "最长 2 天" in sys_p
    assert "600036" in user_p


def test_save_tokenless_preset_without_key(tmp_path):
    """本地预设不填 Key 也能保存；且会把旧云端 Key 清掉（不发到别的机器）。"""
    import paper_trading.cli as hb
    from paper_trading.llm import load_secrets

    sec = str(tmp_path / "s.json")
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml", secrets_path=sec)
    assert b.llm_save("glm", "", "glm-4.5-flash", "sk-cloud-SECRET")["ok"]
    r = b.llm_save("ollama", "", "qwen3:8b", "")       # 切到本机 Ollama
    assert r["ok"] and r["has_key"] is False
    assert r["tokenless"] is True
    assert "127.0.0.1:11434" in r["base_url"]
    assert load_secrets(sec)["provider"]["api_key"] == ""   # 旧云端 Key 已清
    assert b.llm_status()["model"] == "qwen3:8b"


def test_save_cloud_without_key_warns_but_saves(tmp_path):
    import paper_trading.cli as hb

    sec = str(tmp_path / "s.json")
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml", secrets_path=sec)
    r = b.llm_save("deepseek", "", "deepseek-chat", "")
    assert r["ok"] and "warning" in r and "API Key" in r["warning"]
    assert r["has_key"] is False


def test_llm_models_without_key_against_stub(tmp_path):
    """只写地址（无令牌）能拉到列表；已存模型不在列表时给提示。"""
    import paper_trading.cli as hb
    from paper_trading.llm import save_secrets

    srv = _srv()
    try:
        sec = str(tmp_path / "s.json")
        b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                             account_db=str(tmp_path / "a.db"),
                             config_path="/nonexistent.yaml", secrets_path=sec)
        save_secrets({"provider": {"preset": "ollama",
                                   "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                                   "model": "已下线的模型", "api_key": ""}}, sec)
        r = b.llm_models()
        assert r["ok"] and r["models"] == ["qwen3:8b", "llama3.2"]
        assert "不在厂商列表" in r.get("warning", "")
        assert "建议" not in r.get("warning", "")  # 不催用户换模型
    finally:
        srv.shutdown()


@pytest.fixture()
def panel_with_llm(tmp_path):
    """起一个真面板，密钥文件指向本地免令牌桩服务（返回 base_url, 桩端口）。"""
    import paper_trading.dashboard as d
    from paper_trading.cli import TradingBridge
    from paper_trading.llm.secrets import save_secrets

    stub = _srv()
    save_secrets({"provider": {"preset": "ollama",
                               "base_url": f"http://127.0.0.1:{stub.server_port}/v1",
                               "model": "qwen3:8b", "api_key": ""}},
                 str(tmp_path / "s.json"))
    TradingBridge(data_db=str(tmp_path / "data.db"),
                  account_db=str(tmp_path / "acct.db"),
                  config_path="/nonexistent.yaml",
                  secrets_path=str(tmp_path / "s.json"))

    class _H(d.Handler):
        pass

    _H.data_db = str(tmp_path / "data.db")
    _H.account_db = str(tmp_path / "acct.db")
    _H.secrets_path = str(tmp_path / "s.json")
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", stub.server_port
    srv.shutdown()
    srv.server_close()
    stub.shutdown()


def test_dashboard_models_endpoint_needs_no_token(panel_with_llm):
    """面板拉模型：不配 Key 也能通过（且不给口令门禁拦）。"""
    import urllib.request

    base, _ = panel_with_llm
    with urllib.request.urlopen(f"{base}/api/llm/models", timeout=20) as r:
        data = json.loads(r.read().decode())
    assert data["ok"] and data["models"] == ["qwen3:8b", "llama3.2"]
