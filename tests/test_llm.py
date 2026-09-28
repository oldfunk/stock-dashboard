"""LLM 接入层单测（本地 fake HTTP 服务 + 临时 secrets 文件，离线可跑）。"""
import json
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.llm import (  # noqa: E402
    LLMConfig,
    LLMError,
    chat,
    list_models,
    load_secrets,
    masked_key,
    public_provider_view,
    save_provider,
)


class _FakeAPI(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/v1/models":
            if self.headers.get("Authorization") != "Bearer good-key":
                return self._send(401, {"error": "bad key"})
            return self._send(200, {"data": [{"id": "m-a"}, {"id": "m-b"}]})
        return self._send(404, {})

    def do_POST(self):  # noqa: N802
        if self.path == "/v1/chat/completions":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["model"] == "m-a"
            assert body["messages"][-1] == {"role": "user", "content": "你好"}
            return self._send(200, {"choices": [{"message": {"content": "您好"}}]})
        return self._send(404, {})

    def log_message(self, *a):
        pass


def _srv():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAPI)
    Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _cfg(srv, **kw):
    args = {"base_url": f"http://127.0.0.1:{srv.server_port}/v1",
            "api_key": "good-key", "model": "m-a"}
    args.update(kw)
    return LLMConfig(**args)


def test_list_models_ok_and_auth_fail():
    srv = _srv()
    try:
        assert list_models(_cfg(srv)) == ["m-a", "m-b"]
        try:
            list_models(_cfg(srv, api_key="sk-test-SECRETKEY-999"))
            raise AssertionError("should raise")
        except LLMError as e:
            assert "401" in str(e)
            # Key 原文永不出现在错误信息里（服务端回显的 "bad key" 与 Key 无关）
            assert "sk-test-SECRETKEY-999" not in str(e)
    finally:
        srv.shutdown()


def test_chat_ok_and_missing_model():
    srv = _srv()
    try:
        assert chat(_cfg(srv), [{"role": "user", "content": "你好"}]) == "您好"
        try:
            chat(_cfg(srv, model=""), [{"role": "user", "content": "x"}])
            raise AssertionError("should raise")
        except LLMError as e:
            assert "未指定模型" in str(e)
    finally:
        srv.shutdown()


def test_secrets_roundtrip_masked_and_mode(tmp_path):
    f = str(tmp_path / "s.json")
    save_provider({"preset": "deepseek", "base_url": "https://x/v1",
                   "model": "m", "api_key": "sk-secret-1234"}, f)
    data = load_secrets(f)
    assert data["provider"]["api_key"] == "sk-secret-1234"
    assert data["admin_token"]  # 自动生成口令
    view = public_provider_view(f)
    assert view["key_masked"] == "****1234" and view["has_key"]
    assert "sk-secret-1234" not in json.dumps(view)  # 安全视图无原文
    assert "admin_token" not in json.dumps(view)  # 口令也不回显
    assert masked_key("") == ""
    import os
    if os.name == "posix":
        assert oct(os.stat(f).st_mode & 0o777) == "0o600"


def test_llm_save_merge_keeps_key(tmp_path):
    """换模型不重填 Key：空 Key=沿用；首次无 Key 拒绝；换 Key 生效。"""
    import paper_trading.hermes_bridge as hb

    sec = str(tmp_path / "s.json")
    db1 = str(tmp_path / "d.db")
    db2 = str(tmp_path / "a.db")
    b = hb.HermesBridge(data_db=db1, account_db=db2, config_path="/nonexistent.yaml",
                        secrets_path=sec)
    r = b.llm_save("deepseek", "", "", "")
    assert not r["ok"] and "API Key" in r["error"]  # 首次必须给 Key
    r = b.llm_save("deepseek", "", "m-a", "sk-keep-1")
    assert r["ok"] and r["key_masked"] == "****ep-1"
    assert b.llm_status()["model"] == "m-a"
    r = b.llm_save("deepseek", "", "m-b", "")  # 只换模型
    assert r["ok"] and r["model"] == "m-b"
    from paper_trading.llm import load_secrets
    assert load_secrets(sec)["provider"]["api_key"] == "sk-keep-1"  # Key 未被洗掉
    r = b.llm_save("qwen", "", "q", "sk-new-2")  # 换厂商+换 Key
    assert r["ok"] and "dashscope" in r["base_url"]
    assert load_secrets(sec)["provider"]["api_key"] == "sk-new-2"


def test_llm_ask_injects_project_context(monkeypatch, tmp_path):
    """ask 必须把项目快照塞进 system，且不泄露 Key。"""
    import paper_trading.hermes_bridge as hb
    from paper_trading.llm import provider as prov

    captured: dict = {}

    def fake_chat(cfg, messages, system=""):
        captured["system"] = system
        captured["messages"] = messages
        assert cfg.model == "m"
        return "答"

    monkeypatch.setattr(prov, "chat", fake_chat)
    b = hb.HermesBridge(data_db=str(tmp_path / "d.db"),
                        account_db=str(tmp_path / "a.db"),
                        config_path="/nonexistent.yaml",
                        secrets_path=str(tmp_path / "s.json"))
    b.llm_save("deepseek", "https://x/v1", "m", "sk-SECRET")
    r = b.llm_ask("持仓怎么样")
    assert r["ok"] and r["answer"] == "答"
    assert captured["messages"] == [{"role": "user", "content": "持仓怎么样"}]
    sys_text = captured["system"]
    for key in ("account", "positions", "recent_closes", "recent_nav", "recent_ops"):
        assert key in sys_text, key
    assert "sk-SECRET" not in sys_text  # 上下文无 Key
    ops = b.broker.get_op_log(5)
    assert ops[0]["action"] == "llm:ask"
