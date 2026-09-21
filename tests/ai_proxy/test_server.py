"""t4: 外部 AI 代理服务单测。

覆盖：
- /api/analyze 端点（mock LLM 响应）
- /api/health 端点
- 主通道失败 → 降级通道
- 主通道+降级通道均失败 → 错误响应
"""

import json
import pytest
from fastapi.testclient import TestClient

from src.ai_proxy.server import app


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
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    return TestClient(app)


def _req():
    return {
        "code": "600519",
        "name": "贵州茅台",
        "run_id": "20260920_153000",
        "data": {
            "score": 88.5, "pe": 28.5, "pb": 9.2,
            "roe_5y_avg": 25.3, "gross_margin": 92.0,
            "fcf_5y_sum": 1234.5, "market_cap": 2000.0,
        },
    }


class TestAiProxy:
    def test_health(self, client):
        """健康检查"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_analyze_success(self, client, monkeypatch):
        """主通道成功"""
        def mock_call_llm(prompt, api_base, model, api_key=None, timeout=300):
            return {
                "analysis": {"analysis": "测试分析", "trade_strategy": {"signal": "BUY"}},
                "model": "test-model",
                "usage": {"tokens": 100, "cost_usd": 0.01},
            }
        monkeypatch.setattr("src.ai_proxy.server._call_llm", mock_call_llm)
        resp = client.post("/api/analyze", json=_req())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["model"] == "test-model"
        assert data["analysis"]["trade_strategy"]["signal"] == "BUY"

    def test_analyze_fallback(self, client, monkeypatch):
        """主通道失败 → 降级通道成功"""
        call_count = {"primary": 0, "fallback": 0}

        def mock_call_llm(prompt, api_base, model, api_key=None, timeout=300):
            if "openrouter" in api_base:
                call_count["primary"] += 1
                raise Exception("主通道不可用")
            else:
                call_count["fallback"] += 1
                return {
                    "analysis": {"analysis": "降级分析"},
                    "model": "fallback-model",
                    "usage": {"tokens": 50},
                }
        monkeypatch.setattr("src.ai_proxy.server._call_llm", mock_call_llm)
        resp = client.post("/api/analyze", json=_req())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["model"] == "fallback-model"
        assert call_count["primary"] == 1
        assert call_count["fallback"] == 1

    def test_analyze_all_fail(self, client, monkeypatch):
        """主通道+降级通道均失败 → 错误响应"""
        def mock_call_llm(prompt, api_base, model, api_key=None, timeout=300):
            raise Exception("全部不可用")
        monkeypatch.setattr("src.ai_proxy.server._call_llm", mock_call_llm)
        resp = client.post("/api/analyze", json=_req())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert data["retryable"] is True

    def test_analyze_invalid_json_response(self, client, monkeypatch):
        """LLM 返回非 JSON → 尝试提取或返回 raw"""
        def mock_call_llm(prompt, api_base, model, api_key=None, timeout=300):
            return {
                "analysis": {"raw": "非 JSON 文本"},
                "model": "test-model",
                "usage": {"tokens": 10},
            }
        monkeypatch.setattr("src.ai_proxy.server._call_llm", mock_call_llm)
        resp = client.post("/api/analyze", json=_req())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "raw" in data["analysis"]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
