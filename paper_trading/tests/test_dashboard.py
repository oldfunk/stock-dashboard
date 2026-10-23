"""面板 import 门禁：dashboard.py 必须可导入（语法错误直接发版会导致 Pi 服务崩溃循环）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_dashboard_imports():
    import paper_trading.dashboard as d

    assert hasattr(d, "Handler") and hasattr(d, "main")
    assert "market-row" in d.PAGE and "/api/quotes" in d.PAGE
    assert "运行时刻" in d.PAGE and "/api/agent/runtime" in d.PAGE
    assert "/api/agent/schedule" not in d.PAGE  # 旧可编辑队列已从界面移除
    assert "写死" in d.PAGE and "16:30" in d.PAGE


def test_entry_modules_import():
    """所有入口模块必须可导入（曾带着语法错误发版过）。"""
    import paper_trading.agent.loop
    import paper_trading.cli
    import paper_trading.main

    assert hasattr(paper_trading.cli, "main")
    assert hasattr(paper_trading.agent.loop, "AgentTrader")


def test_schemes_have_source():
    """Scheme 必带 source/allow_buy（cli scheme list 序列化依赖；缺失曾 500）。"""
    from paper_trading.strategy.schemes import all_schemes
    ss = all_schemes()
    assert set(ss) == {"mother", "general", "custom"} or set(ss) >= {"general", "custom"}
    for name, s in ss.items():
        assert s.source, name
        assert isinstance(s.allow_buy, bool), name


def test_models_endpoint_has_no_token_gate():
    """拉模型不得被口令门禁拦（用户抱怨点：重启后提示"必须保存 Key 才能拉"）。
    源码级锁定：/api/llm/models 分支内不得出现 _admin_ok / 403。"""
    import inspect

    import paper_trading.dashboard as d

    src = inspect.getsource(d.Handler.do_GET)
    start = src.index('u.path == "/api/llm/models"')
    seg = src[start:start + 700]
    seg = seg.split("elif u.path", 1)[0]
    assert "_admin_ok" not in seg
    assert "403" not in seg
    # 前端不得再有"先保存配置才能拉"的拦截
    assert "请先保存一次配置" not in d.PAGE
    assert '/api/llm/models"' in d.PAGE  # 直接拉，不带 token


@pytest.fixture
def cloud_panel(tmp_path):
    """真面板 + 已存云端 Key 的密钥文件（模拟"换浏览器/换地址后口令不在"）。"""
    import json as _json
    import threading
    import urllib.error
    import urllib.request
    from http.server import HTTPServer

    import paper_trading.dashboard as d
    from paper_trading.llm.secrets import save_provider

    sec = str(tmp_path / "s.json")
    save_provider({"preset": "glm",
                   "base_url": "https://open.bigmodel.cn/api/paas/v4",
                   "model": "glm-4.5-flash", "api_key": "sk-SAVED-KEY"}, sec)

    class _H(d.Handler):
        pass

    _H.data_db = str(tmp_path / "data.db")
    _H.account_db = str(tmp_path / "acct.db")
    _H.secrets_path = sec
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def call(body):
        req = urllib.request.Request(base + "/api/llm/config",
                                     data=_json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, _json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, _json.loads(e.read().decode())

    yield base, call, sec
    srv.shutdown()
    srv.server_close()


def test_llm_config_model_only_needs_no_token(cloud_panel):
    """"换模型不用口令"：只改模型名直接保存成功，已存 Key 沿用、口令顺带领回。

    真实痛点：口令只存浏览器 localStorage，换浏览器/换访问地址就丢；
    旧逻辑连改个模型名都要口令 → 反复要求重输已保存的 Key。
    """
    _, call, sec = cloud_panel
    st, r = call({"preset": "glm", "base_url": "https://open.bigmodel.cn/api/paas/v4",
                  "model": "glm-5.3-flash", "api_key": ""})
    assert st == 200 and r["ok"] and r["model"] == "glm-5.3-flash"
    assert r["key_masked"] == "****-KEY"      # 已存 Key 没被洗掉（掩码只露末 4 位）
    assert r["admin_token"]                    # 浏览器存下它，下次改地址也不用再输 Key


def test_llm_config_base_url_change_requires_token(cloud_panel):
    """改接口地址（会把已存 Key 发出去的地方）必须口令或凭已存 Key 接管。"""
    _, call, sec = cloud_panel
    st, r = call({"preset": "glm", "base_url": "https://evil.test/v1",
                  "model": "glm-5.3-flash", "api_key": ""})
    assert st == 403 and "口令" in r["error"]
    import json as _json

    saved = _json.loads(Path(sec).read_text(encoding="utf-8"))
    assert saved["provider"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"


def test_llm_config_takeover_accepts_any_key(cloud_panel):
    """兼容性优先：接管改地址接受任意非空 Key（各家 Key 字段差异大，不做强一致校验）。"""
    _, call, sec = cloud_panel
    st, r = call({"preset": "glm", "base_url": "https://new-provider.test/v1",
                  "model": "glm-5.3-flash", "api_key": "sk-ANY-FORMAT-KEY"})
    assert st == 200 and r["ok"] and r["admin_token"]


def test_read_json_bad_body_raises():
    """坏 JSON 必须报真实原因（以前静默变空 body → 用户看到误导性报错）。"""
    import io

    import paper_trading.dashboard as d

    h = d.Handler.__new__(d.Handler)          # 不跑 __init__（不绑 socket）
    h.headers = {"Content-Length": "7"}
    h.rfile = io.BytesIO(b"{bad js")
    with pytest.raises(ValueError) as ei:
        h._read_json()
    assert "合法 JSON" in str(ei.value)
    h.headers = {"Content-Length": "0"}
    h.rfile = io.BytesIO(b"")
    assert h._read_json() == {}
    h.headers = {"Content-Length": "2"}
    h.rfile = io.BytesIO(b"[]")
    with pytest.raises(ValueError):
        h._read_json()


def test_takeover_token(tmp_path):
    """凭 Key 接管：对 Key 放行并返回可存口令；错 Key/空配置拒绝。"""
    from paper_trading.llm.secrets import save_provider
    from paper_trading.dashboard import _takeover_token
    p = save_provider({"preset": "custom", "base_url": "https://x.test/v1",
                       "model": "m", "api_key": "sk-fake-key"},
                      path=str(tmp_path / "s.json"))
    import json as _json
    stored = _json.loads(p.read_text(encoding="utf-8"))
    token = stored["admin_token"]
    assert token
    assert _takeover_token({"api_key": "sk-fake-key"}, str(tmp_path / "s.json")) == token
    assert _takeover_token({"api_key": "wrong"}, str(tmp_path / "s.json")) == ""
    assert _takeover_token({}, str(tmp_path / "s.json")) == ""
    assert _takeover_token({"api_key": "k"}, str(tmp_path / "nope.json")) == ""
