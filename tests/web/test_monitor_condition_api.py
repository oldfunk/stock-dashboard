"""M3-2d 监控条件 API 端点单测。

覆盖 GET /api/watchlist/{code}/monitor 和 POST /api/watchlist/{code}/monitor：
- GET 读取条件
- POST 更新条件
- POST 清空条件（null）
- POST 非法类型 → 400
- 不存在的 code → 404
"""

import json
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
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _add_to_ai_watchlist(code: str, name: str = "测试股票"):
    """直接往 ai_watchlist 表添加股票（监控条件挂在 AI 观察池）"""
    from src.models.ai_watchlist import AiWatchlistDAO
    AiWatchlistDAO().add(code, name, added_reason="测试用")


class TestMonitorConditionAPI:
    def test_get_monitor_condition(self, client):
        """GET 读取监控条件"""
        _add_to_ai_watchlist("600519")
        resp = client.get("/api/watchlist/600519/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "600519"
        assert data["monitor_condition"] is None

    def test_update_monitor_condition(self, client):
        """POST 更新监控条件"""
        _add_to_ai_watchlist("600519")
        cond = json.dumps({"metric": "pe", "operator": "lt", "threshold": 10})
        resp = client.post("/api/watchlist/600519/monitor",
                           json={"monitor_condition": cond})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["monitor_condition"] == cond

        # 验证 GET 能读回
        resp2 = client.get("/api/watchlist/600519/monitor")
        assert resp2.json()["monitor_condition"] == cond

    def test_clear_monitor_condition(self, client):
        """POST null 清空监控条件"""
        _add_to_ai_watchlist("600519")
        cond = json.dumps({"metric": "pe", "operator": "lt", "threshold": 10})
        client.post("/api/watchlist/600519/monitor",
                    json={"monitor_condition": cond})
        resp = client.post("/api/watchlist/600519/monitor",
                           json={"monitor_condition": None})
        assert resp.status_code == 200
        assert resp.json()["monitor_condition"] is None

    def test_invalid_type_400(self, client):
        """POST 非法类型 → 400"""
        _add_to_ai_watchlist("600519")
        resp = client.post("/api/watchlist/600519/monitor",
                           json={"monitor_condition": 123})
        assert resp.status_code == 400

    def test_nonexistent_code_404(self, client):
        """不存在的 code → 404"""
        resp = client.post("/api/watchlist/999999/monitor",
                           json={"monitor_condition": "{}"})
        assert resp.status_code == 404


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
