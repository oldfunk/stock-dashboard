"""LLM 自带 Key 接入后端单测（OpenAI-compatible v1）。

覆盖：
- PROVIDERS 预设表合法性（id 唯一、base 为 http(s)、custom 兜底）
- fetch_models：正常列表 / 401 / 非 200 / 连接异常 / /chat/completions 后缀剥离
- save_llm_config：.env upsert 保留其他行、local.yaml 只动 ai 段、os.environ 即时生效、空 key 不覆盖
- llm_status：脱敏（原值永不出现）
- 路由：/api/llm/providers、/test（mock）、/save（脱敏断言）、/save 非法参数 400
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个测试用独立临时数据库"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"data": []}

    def json(self):
        return self._payload


def _fake_client_factory(holder):
    """holder: dict(url=..., headers=..., resp|exc=...)，FakeClient 读它。"""
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            holder["url"] = url
            holder["headers"] = headers
            if "exc" in holder:
                raise holder["exc"]
            return holder["resp"]
    return _FakeClient


class TestProviders:
    def test_presets_sane(self):
        from src.llm_config import PROVIDERS
        ids = [p["id"] for p in PROVIDERS]
        assert len(ids) == len(set(ids))
        assert "custom" in ids
        for p in PROVIDERS:
            if p["id"] == "custom":
                assert p["api_base"] == ""
            else:
                assert p["api_base"].startswith(("http://", "https://"))
                assert not p["api_base"].endswith("/chat/completions")


class TestFetchModels:
    def test_ok(self, monkeypatch):
        import httpx
        from src import llm_config
        holder = {"resp": _FakeResp(200, {"data": [{"id": "m1"}, {"id": "m2"}]})}
        monkeypatch.setattr(httpx, "Client", _fake_client_factory(holder))
        assert llm_config.fetch_models("https://x.test/v1", "k") == ["m1", "m2"]
        assert holder["url"] == "https://x.test/v1/models"
        assert holder["headers"]["Authorization"] == "Bearer k"

    def test_strips_chat_suffix(self, monkeypatch):
        import httpx
        from src import llm_config
        holder = {"resp": _FakeResp(200, {"data": [{"id": "m"}]})}
        monkeypatch.setattr(httpx, "Client", _fake_client_factory(holder))
        assert llm_config.fetch_models("https://x.test/v1/chat/completions", "") == ["m"]
        assert holder["url"] == "https://x.test/v1/models"
        assert "Authorization" not in holder["headers"]

    def test_unauthorized(self, monkeypatch):
        import httpx
        from src import llm_config
        holder = {"resp": _FakeResp(401, {})}
        monkeypatch.setattr(httpx, "Client", _fake_client_factory(holder))
        with pytest.raises(llm_config.LLMSetupError, match="401"):
            llm_config.fetch_models("https://x.test/v1", "bad")

    def test_http_error(self, monkeypatch):
        import httpx
        from src import llm_config
        holder = {"resp": _FakeResp(500, {})}
        monkeypatch.setattr(httpx, "Client", _fake_client_factory(holder))
        with pytest.raises(llm_config.LLMSetupError, match="500"):
            llm_config.fetch_models("https://x.test/v1", "k")

    def test_conn_error_has_no_key(self, monkeypatch):
        import httpx
        from src import llm_config
        holder = {"exc": ConnectionError("boom")}
        monkeypatch.setattr(httpx, "Client", _fake_client_factory(holder))
        with pytest.raises(llm_config.LLMSetupError) as ei:
            llm_config.fetch_models("https://x.test/v1", "sk-secret-xyz")
        assert "sk-secret-xyz" not in str(ei.value)

    def test_bad_scheme(self):
        from src import llm_config
        with pytest.raises(llm_config.LLMSetupError):
            llm_config.fetch_models("ftp://x.test", "")

    def test_empty_list(self, monkeypatch):
        import httpx
        from src import llm_config
        holder = {"resp": _FakeResp(200, {"data": []})}
        monkeypatch.setattr(httpx, "Client", _fake_client_factory(holder))
        with pytest.raises(llm_config.LLMSetupError, match="手动输入"):
            llm_config.fetch_models("https://x.test/v1", "k")


class TestSaveAndStatus:
    def test_roundtrip(self, tmp_path, monkeypatch):
        from src import llm_config
        env = tmp_path / ".env"
        env.write_text("OTHER=keep\nSTOCK_AI_API_KEY=old\n", encoding="utf-8")
        local = tmp_path / "local.yaml"
        for name in (llm_config.ENV_KEY, llm_config.ENV_BASE, llm_config.ENV_MODEL):
            monkeypatch.delenv(name, raising=False)
        st = llm_config.save_llm_config(
            "deepseek", "https://api.deepseek.com/v1", "deepseek-chat",
            api_key="sk-new-key-1234567890", temperature=0.3, max_tokens=6000,
            env_path=env, local_path=local)
        text = env.read_text(encoding="utf-8")
        assert "OTHER=keep" in text
        assert "STOCK_AI_API_BASE=https://api.deepseek.com/v1" in text
        assert "STOCK_AI_MODEL=deepseek-chat" in text
        assert "sk-new-key-1234567890" in text
        import os
        assert os.environ[llm_config.ENV_KEY] == "sk-new-key-1234567890"
        assert st["configured"] is True
        assert st["provider"] == "deepseek"
        assert "sk-new-key-1234567890" not in str(st)
        assert "****" in st["key_preview"]

    def test_empty_key_keeps_old(self, tmp_path, monkeypatch):
        from src import llm_config
        import os
        env = tmp_path / ".env"
        env.write_text("", encoding="utf-8")
        monkeypatch.setenv(llm_config.ENV_KEY, "sk-keep-me-1234567890")
        for name in (llm_config.ENV_BASE, llm_config.ENV_MODEL):
            monkeypatch.delenv(name, raising=False)
        llm_config.save_llm_config(
            "custom", "https://x.test/v1", "m", api_key="",
            env_path=env, local_path=tmp_path / "local.yaml")
        assert os.environ[llm_config.ENV_KEY] == "sk-keep-me-1234567890"
        assert "STOCK_AI_API_KEY" not in env.read_text(encoding="utf-8")

    def test_model_required(self, tmp_path):
        from src import llm_config
        with pytest.raises(llm_config.LLMSetupError, match="模型名"):
            llm_config.save_llm_config(
                "custom", "https://x.test/v1", "  ",
                env_path=tmp_path / ".env",
                local_path=tmp_path / "local.yaml")

    def test_status_no_key(self, monkeypatch):
        from src import llm_config
        for name in (llm_config.ENV_KEY, llm_config.ENV_BASE, llm_config.ENV_MODEL):
            monkeypatch.delenv(name, raising=False)
        st = llm_config.llm_status()
        assert st["configured"] is False
        assert st["has_key"] is False


class TestRoutes:
    def test_providers(self, client):
        resp = client.get("/api/llm/providers")
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()["providers"]]
        assert "deepseek" in ids and "custom" in ids

    def test_test_endpoint_ok(self, client, monkeypatch):
        from src import llm_config
        monkeypatch.setattr(
            llm_config, "fetch_models", lambda base, key="": ["a", "b"])
        resp = client.post("/api/llm/test",
                           json={"api_base": "https://x.test/v1", "api_key": "k"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "models": ["a", "b"]}

    def test_test_endpoint_fail(self, client, monkeypatch):
        from src import llm_config

        def _boom(base, key=""):
            raise llm_config.LLMSetupError("连接失败：Timeout")
        monkeypatch.setattr(llm_config, "fetch_models", _boom)
        resp = client.post("/api/llm/test",
                           json={"api_base": "https://x.test/v1"})
        assert resp.json()["ok"] is False

    def test_save_masked(self, client, monkeypatch):
        from src import llm_config

        def _fake_save(*args, **kwargs):
            assert args[3] == "sk-raw-secret-1234567890"
            return {"configured": True, "has_key": True,
                    "key_preview": "sk-r****7890", "api_base": "https://x.test/v1",
                    "model": "m", "provider": "custom"}
        monkeypatch.setattr(llm_config, "save_llm_config", _fake_save)
        resp = client.post("/api/llm/save", json={
            "provider": "custom", "api_base": "https://x.test/v1",
            "model": "m", "api_key": "sk-raw-secret-1234567890"})
        assert resp.status_code == 200
        assert "sk-raw-secret-1234567890" not in resp.text

    def test_save_bad_base_400(self, client):
        resp = client.post("/api/llm/save", json={
            "provider": "custom", "api_base": "not-a-url", "model": "m"})
        assert resp.status_code == 400

    def test_status_shape(self, client):
        resp = client.get("/api/llm/status")
        assert resp.status_code == 200
        for k in ("configured", "has_key", "key_preview",
                  "api_base", "model", "provider"):
            assert k in resp.json()


def _reset_busy():
    from src.web import routes as routes_mod
    routes_mod._ai_state["running"] = False


def _seed_run(codes):
    """插 run_log completed + screening 行；codes: [(code, name, ai_failed)]。"""
    from src.models import database as db_mod
    db_mod.RunLogDAO().start_run("r-test")
    db_mod.RunLogDAO().complete_run("r-test", 5527, len(codes), 0)
    with db_mod.db_conn() as conn:
        for code, name, failed in codes:
            conn.execute(
                "INSERT INTO screening_result "
                "(run_id, run_date, code, name, score, ai_failed)"
                " VALUES (?,?,?,?,?,?)",
                ("r-test", "2026-09-27", code, name, 90.0, failed))


class _FakeAnalyzer:
    def __init__(self, cfg=None, ok=True):
        self._ok = ok

    @property
    def configured(self):
        return self._ok


class TestAnalyze:
    def test_bad_mode(self, client):
        _reset_busy()
        resp = client.post("/api/llm/analyze", json={"mode": "nope"})
        assert resp.status_code == 400

    def test_unconfigured(self, client, monkeypatch):
        _reset_busy()
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.AiAnalyzer",
            lambda cfg=None: _FakeAnalyzer(ok=False))
        resp = client.post("/api/llm/analyze", json={"mode": "all"})
        assert resp.status_code == 400
        assert "API Key" in resp.json()["detail"]

    def test_busy_409(self, client):
        from src.web import routes as routes_mod
        routes_mod._ai_state["running"] = True
        try:
            resp = client.post("/api/llm/analyze", json={"mode": "all"})
            assert resp.status_code == 409
        finally:
            routes_mod._ai_state["running"] = False

    def test_once_unknown_code_404(self, client, monkeypatch):
        _reset_busy()
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.AiAnalyzer",
            lambda cfg=None: _FakeAnalyzer(ok=True))
        _seed_run([("600519", "贵州茅台", 0)])
        resp = client.post("/api/llm/analyze",
                           json={"mode": "once", "code": "000001"})
        assert resp.status_code == 404

    def test_once_started(self, client, monkeypatch):
        import time
        _reset_busy()
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.AiAnalyzer",
            lambda cfg=None: _FakeAnalyzer(ok=True))
        calls = {}

        def _fake_batch(stocks, run_id, interval_seconds=60, on_progress=None):
            calls["n"] = len(stocks)
            calls["interval"] = interval_seconds
            if on_progress:
                on_progress(len(stocks), 0, len(stocks) - 1)
            return len(stocks), 0
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.analyze_batch", _fake_batch)
        _seed_run([("600519", "贵州茅台", 0)])
        resp = client.post("/api/llm/analyze",
                           json={"mode": "once", "code": "600519",
                                 "interval_seconds": 5})
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        assert resp.json()["total"] == 1
        for _ in range(40):
            if calls.get("n"):
                break
            time.sleep(0.05)
        assert calls.get("n") == 1
        assert calls.get("interval") == 5

    def test_retry_noop(self, client, monkeypatch):
        _reset_busy()
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.AiAnalyzer",
            lambda cfg=None: _FakeAnalyzer(ok=True))
        _seed_run([("600519", "贵州茅台", 0)])
        resp = client.post("/api/llm/analyze", json={"mode": "retry"})
        assert resp.json()["status"] == "noop"

    def test_retry_picks_failed(self, client, monkeypatch):
        import time
        _reset_busy()
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.AiAnalyzer",
            lambda cfg=None: _FakeAnalyzer(ok=True))
        calls = {}

        def _fake_batch(stocks, run_id, interval_seconds=60, on_progress=None):
            calls["codes"] = sorted(s["code"] for s in stocks)
            return len(stocks), 0
        monkeypatch.setattr(
            "src.analyzer.ai_analyzer.analyze_batch", _fake_batch)
        _seed_run([("600519", "贵州茅台", 0), ("000858", "五粮液", 1)])
        resp = client.post("/api/llm/analyze", json={"mode": "retry"})
        assert resp.json()["status"] == "started"
        assert resp.json()["total"] == 1
        for _ in range(40):
            if calls.get("codes"):
                break
            time.sleep(0.05)
        assert calls.get("codes") == ["000858"]


class TestUsageAndPage:
    def test_usage_totals(self, client):
        from src.models import database as db_mod
        dao = db_mod.AiAnalysisLogDAO()
        dao.log("r1", "600519", "m", 100, 50, 0.0)
        dao.log("r1", "000858", "m", 200, 60, 0.0)
        resp = client.get("/api/llm/usage", params={"run_id": "r1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["total_prompt"] == 300
        assert data["total_completion"] == 110

    def test_usage_empty(self, client):
        resp = client.get("/api/llm/usage")
        assert resp.json()["rows"] == []

    def test_llm_page(self, client):
        resp = client.get("/llm")
        assert resp.status_code == 200
        assert "模型设置" in resp.text
