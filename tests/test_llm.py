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
    """换模型不重填 Key：空 Key=沿用；没 Key 也能先存（带提示）；换 Key 生效。"""
    import paper_trading.cli as hb

    sec = str(tmp_path / "s.json")
    db1 = str(tmp_path / "d.db")
    db2 = str(tmp_path / "a.db")
    b = hb.TradingBridge(data_db=db1, account_db=db2, config_path="/nonexistent.yaml",
                        secrets_path=sec)
    # 云端没 Key 也能先存（本地接口本来就免令牌），但要给提示
    r = b.llm_save("deepseek", "", "", "")
    assert r["ok"] and not r["has_key"] and "API Key" in r.get("warning", "")
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


def test_llm_models_open_and_explains(tmp_path):
    """拉模型不被门禁拦，且失败必须给可执行原因（没配地址/网络不通/Key被拒）。"""
    import paper_trading.cli as hb
    from paper_trading.llm import load_secrets, save_secrets

    sec = str(tmp_path / "s.json")
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml", secrets_path=sec)
    # 1) 完全没配：给"去保存"的提示，不是"你没权限"
    r = b.llm_models()
    assert not r["ok"] and r["hint"] == "save" and "接口地址" in r["error"]
    # 2) 只填了地址没 Key（本地/局域网场景）：**放行**去拉，而不是拦在"缺 Key"
    srv = _srv()
    try:
        save_secrets({"provider": {"preset": "ollama",
                                   "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                                   "model": "m", "api_key": ""}}, sec)
        r = b.llm_models()  # 桩服务要 Bearer good-key：无令牌被拒 = 真实原因
        assert not r["ok"] and "401" in r["error"] and "hint" not in r
    finally:
        srv.shutdown()
    # 3) 地址不可达：回网络层真实原因
    save_secrets({"provider": {"preset": "custom", "base_url": "http://127.0.0.1:1/v1",
                               "model": "m", "api_key": ""}}, sec)
    r = b.llm_models()
    assert not r["ok"] and "hint" not in r
    # 4) 配全且 Key 正确：拉到列表
    srv = _srv()
    try:
        save_secrets({"provider": {"preset": "custom",
                                   "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                                   "model": "m", "api_key": "good-key"}}, sec)
        r = b.llm_models()
        assert r["ok"] and r["models"] == ["m-a", "m-b"]
        assert load_secrets(sec)["provider"]["api_key"] == "good-key"
    finally:
        srv.shutdown()


def test_secrets_file_with_bom_is_readable(tmp_path):
    """Windows 记事本保存会带 BOM：不能因此把配置读成"没配"（静默失败最难查）。"""
    from paper_trading.llm import load_secrets

    p = tmp_path / "s.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"provider": {"preset": "glm", "base_url": "https://x/v1",
                      "model": "m", "api_key": "sk-bom"}},
        ensure_ascii=False).encode("utf-8"))
    data = load_secrets(str(p))
    assert data["provider"]["api_key"] == "sk-bom"


def test_llm_save_keeps_model_when_blank(tmp_path):
    """模型留空 = 沿用已存模型（不许被"只改 Key/地址"顺手洗成空串）。"""
    import paper_trading.cli as hb
    from paper_trading.llm import load_secrets

    sec = str(tmp_path / "s.json")
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml", secrets_path=sec)
    assert b.llm_save("glm", "", "glm-4.5-flash", "sk-x")["ok"]
    # 只改地址，不给模型
    r = b.llm_save("glm", "https://open.bigmodel.cn/api/paas/v4", "", "")
    assert r["ok"] and r["model"] == "glm-4.5-flash"
    # 只换 Key，不给模型
    r = b.llm_save("glm", "", "", "sk-y")
    assert r["ok"] and r["model"] == "glm-4.5-flash"
    assert load_secrets(sec)["provider"]["model"] == "glm-4.5-flash"


def test_llm_save_survives_restart(tmp_path, monkeypatch):
    """长效保存：保存后用全新进程式读取（重新 load 文件）仍在；换模型不洗掉 Key。"""
    import paper_trading.cli as hb

    sec = str(tmp_path / "s.json")
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml", secrets_path=sec)
    assert b.llm_save("glm", "", "glm-4.5-flash", "sk-persist-9")["ok"]
    # 模拟重启：全新 Bridge 实例（只读同一文件，不带任何内存态）
    b2 = hb.TradingBridge(data_db=str(tmp_path / "d2.db"),
                          account_db=str(tmp_path / "a2.db"),
                          config_path="/nonexistent.yaml", secrets_path=sec)
    st = b2.llm_status()
    assert st["configured"] and st["has_key"] and st["model"] == "glm-4.5-flash"
    assert st["key_masked"] == "****st-9"
    assert b2.llm_save("glm", "", "glm-4-flash", "")["ok"]  # 只换模型
    assert b2.llm_status()["model"] == "glm-4-flash"
    from paper_trading.llm import load_secrets
    assert load_secrets(sec)["provider"]["api_key"] == "sk-persist-9"


def test_ask_anti_hallucination_anchors(monkeypatch, tmp_path):
    """回归：问答不得把「行情快照里的池内股票」当成持仓（曾幻觉持有 10 只，实际 2 只）。"""
    import paper_trading.cli as hb
    from paper_trading.llm import provider as prov

    cap: dict = {}

    def fake_chat(cfg, messages, system=""):
        cap["system"] = system
        cap["user"] = messages[-1]["content"]
        return "答"

    monkeypatch.setattr(prov, "chat", fake_chat)
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.llm_save("deepseek", "https://x/v1", "m", "sk-SECRET")
    b.data_db.upsert_stock_names({"600519": "贵州茅台", "000858": "五粮液"})
    from paper_trading.models import Bar, Order, OrderType

    today = __import__("datetime").datetime.now()
    b.data_db.upsert_bars([
        Bar(symbol="000858", timestamp=today, open=70.0, high=70.5, low=69.5,
            close=70.0, volume=1000),
        Bar(symbol="600519", timestamp=today, open=1500.0, high=1510.0, low=1490.0,
            close=1500.0, volume=1000)])
    res = b.broker.submit_order(Order(symbol="000858", direction=1, volume=100,
                                      order_type=OrderType.LIMIT, limit_price=70.0))
    assert res.status.value == "filled", res.status
    assert b.llm_ask("我持有哪些股票")["ok"]
    sys_text, user_text = cap["system"], cap["user"]
    # 1) 硬规则在
    assert "只有上面「当前真实持仓」" in sys_text
    assert "数据未提供" in sys_text
    # 2) 持仓权威段置顶，且只列真实持仓
    assert "当前真实持仓（共 1 只" in sys_text
    assert "五粮液(000858)" in sys_text
    assert "贵州茅台" not in sys_text.split("项目实时状态")[0]  # 未持仓的不得进持仓段
    # 3) 用户侧也带真实持仓切片
    assert "五粮液(000858)" in user_text
    # 4) JSON 上下文带 legend（区分持仓与行情参考）
    assert '"legend"' in sys_text
    assert "不代表持仓" in sys_text


def test_ask_holdings_block_empty_portfolio():
    import paper_trading.cli as hb

    assert "空仓" in hb._holdings_block({"positions": []})
    ctx = {"positions": [{"stock": "五粮液(000858)", "total": 200, "available": 200,
                          "avg_cost": 69.0, "price": 70.0, "market_value": 14000}]}
    blk = hb._holdings_block(ctx)
    assert "共 1 只" in blk and "200股" in blk and "69" in blk


def test_ask_ask_rules_always_applied(monkeypatch, tmp_path):
    """自定义 system 也不能把防幻觉硬规则挤掉。"""
    import paper_trading.cli as hb
    from paper_trading.llm import provider as prov

    cap: dict = {}
    monkeypatch.setattr(prov, "chat",
                        lambda cfg, m, system="": cap.setdefault("s", system) or "答")
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.llm_save("deepseek", "https://x/v1", "m", "sk-SECRET")
    b.llm_ask("随便问问", system="你是辣椒炒肉机器人")
    assert "辣椒炒肉机器人" in cap["s"]
    assert "只有上面「当前真实持仓」" in cap["s"]


def test_llm_ask_injects_project_context(monkeypatch, tmp_path):
    """ask 必须把项目快照塞进 system，且不泄露 Key。"""
    import paper_trading.cli as hb
    from paper_trading.llm import provider as prov

    captured: dict = {}

    def fake_chat(cfg, messages, system=""):
        captured["system"] = system
        captured["messages"] = messages
        assert cfg.model == "m"
        return "答"

    monkeypatch.setattr(prov, "chat", fake_chat)
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                        account_db=str(tmp_path / "a.db"),
                        config_path="/nonexistent.yaml",
                        secrets_path=str(tmp_path / "s.json"))
    b.llm_save("deepseek", "https://x/v1", "m", "sk-SECRET")
    r = b.llm_ask("持仓怎么样")
    assert r["ok"] and r["answer"] == "答"
    assert captured["messages"][0]["role"] == "user"
    assert captured["messages"][0]["content"].startswith("持仓怎么样")
    assert '"positions"' in captured["messages"][0]["content"]  # 附真实持仓切片
    sys_text = captured["system"]
    for key in ("account", "positions", "recent_closes", "recent_nav", "recent_ops"):
        assert key in sys_text, key
    assert "sk-SECRET" not in sys_text  # 上下文无 Key
    ops = b.broker.get_op_log(5)
    assert ops[0]["action"] == "llm:ask"
