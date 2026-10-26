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
    """"换模型不用口令"：只改模型名直接保存成功，已存 Key 沿用。

    真实痛点：口令只存浏览器 localStorage，换浏览器/换访问地址就丢；
    旧逻辑连改个模型名都要口令 → 反复要求重输已保存的 Key。
    但免口令保存**不回传** admin_token：否则一次改模型就能拿到全部写权限。
    """
    _, call, sec = cloud_panel
    st, r = call({"preset": "glm", "base_url": "https://open.bigmodel.cn/api/paas/v4",
                  "model": "glm-5.3-flash", "api_key": ""})
    assert st == 200 and r["ok"] and r["model"] == "glm-5.3-flash"
    assert r["key_masked"] == "****-KEY"      # 已存 Key 没被洗掉（掩码只露末 4 位）
    assert "admin_token" not in r             # 未鉴权的保存不泄露写入口令


def test_llm_config_model_only_with_valid_token_returns_it(cloud_panel):
    """调用方本来就持有口令时，回传不增加权限（前端可继续刷新 localStorage）。"""
    import json as _json

    _, call, sec = cloud_panel
    tok = _json.loads(Path(sec).read_text(encoding="utf-8"))["admin_token"]
    st, r = call({"admin_token": tok, "preset": "glm",
                  "base_url": "https://open.bigmodel.cn/api/paas/v4",
                  "model": "glm-4.5-flash", "api_key": ""})
    assert st == 200 and r["ok"] and r["admin_token"] == tok


def test_llm_config_base_url_change_requires_token(cloud_panel):
    """改接口地址（会把已存 Key 发出去的地方）必须口令或凭**已存** Key 接管。"""
    _, call, sec = cloud_panel
    st, r = call({"preset": "glm", "base_url": "https://evil.test/v1",
                  "model": "glm-5.3-flash", "api_key": ""})
    assert st == 403 and "口令" in r["error"]
    # 随便填一个 Key 不能接管（否则改地址 + 领口令 = 任意写权限）
    st2, r2 = call({"preset": "glm", "base_url": "https://evil.test/v1",
                    "model": "glm-5.3-flash", "api_key": "sk-ANY-KEY"})
    assert st2 == 403 and "口令" in r2["error"]
    import json as _json

    saved = _json.loads(Path(sec).read_text(encoding="utf-8"))
    assert saved["provider"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"


def test_llm_config_takeover_requires_saved_key(cloud_panel):
    """凭 Key 接管必须与当前已存 Key 一致（证明知道密钥），否则拒绝。"""
    _, call, sec = cloud_panel
    st, r = call({"preset": "glm", "base_url": "https://new-provider.test/v1",
                  "model": "glm-5.3-flash", "api_key": "sk-SAVED-KEY"})
    assert st == 200 and r["ok"] and r["admin_token"]
    import json as _json

    saved = _json.loads(Path(sec).read_text(encoding="utf-8"))
    assert saved["provider"]["base_url"] == "https://new-provider.test/v1"


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


def test_llm_config_key_change_requires_token(cloud_panel):
    """未鉴权不能覆盖已存 Key：改 Key → 凭新 Key 接管 = 任意写权限。"""
    import json as _json

    _, call, sec = cloud_panel
    st, r = call({"preset": "glm",
                  "base_url": "https://open.bigmodel.cn/api/paas/v4",
                  "model": "glm-4.5-flash", "api_key": "sk-NEW-KEY"})
    assert st == 403 and "口令" in r["error"]
    saved = _json.loads(Path(sec).read_text(encoding="utf-8"))
    assert saved["provider"]["api_key"] == "sk-SAVED-KEY"

    # 用已存 Key 提交（未变化）只改模型：允许，且不回传口令
    st2, r2 = call({"preset": "glm",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "model": "glm-5.3-flash", "api_key": "sk-SAVED-KEY"})
    assert st2 == 200 and r2["ok"] and "admin_token" not in r2

    # 持口令轮换 Key：允许
    tok = saved["admin_token"]
    st3, r3 = call({"admin_token": tok, "preset": "glm",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "model": "glm-5.3-flash", "api_key": "sk-ROTATED"})
    assert st3 == 200 and r3["ok"]
    saved = _json.loads(Path(sec).read_text(encoding="utf-8"))
    assert saved["provider"]["api_key"] == "sk-ROTATED"


def test_same_origin_guard():
    """跨站 POST（CSRF）拒绝；同源或非浏览器请求放行。"""
    import paper_trading.dashboard as d

    h = d.Handler.__new__(d.Handler)
    h.headers = {"Origin": "http://evil.test", "Host": "192.168.1.10:8080"}
    assert not h._same_origin()
    h.headers = {"Origin": "http://192.168.1.10:8080", "Host": "192.168.1.10:8080"}
    assert h._same_origin()
    h.headers = {}
    assert h._same_origin()


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
