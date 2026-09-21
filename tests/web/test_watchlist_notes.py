"""M3-2d 钉选股票投资笔记 API 端点单测。

覆盖：
- GET /api/watchlist/{code}/notes 获取笔记列表
- POST /api/watchlist/{code}/notes 添加笔记（含 model 溯源）
- GET /api/watchlist/notes 批量获取
- 非法参数 → 400
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
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


class TestWatchlistNotesAPI:
    def test_add_and_get_notes(self, client):
        """添加笔记并获取"""
        # 先钉选一只股票
        client.post("/api/watchlist/600519")
        # 添加笔记
        resp = client.post("/api/watchlist/600519/notes",
                           json={"note": "测试笔记", "note_type": "weekly"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["code"] == "600519"
        assert data["note_type"] == "weekly"

        # 获取笔记列表
        resp2 = client.get("/api/watchlist/600519/notes")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["code"] == "600519"
        assert len(data2["notes"]) == 1
        assert data2["notes"][0]["note"] == "测试笔记"

    def test_get_notes_empty(self, client):
        """无笔记时返回空列表"""
        client.post("/api/watchlist/600519")
        resp = client.get("/api/watchlist/600519/notes")
        assert resp.status_code == 200
        assert resp.json()["notes"] == []

    def test_add_note_invalid_params(self, client):
        """非法参数 → 400"""
        client.post("/api/watchlist/600519")
        # 空 note
        resp = client.post("/api/watchlist/600519/notes", json={"note": ""})
        assert resp.status_code == 400
        # 非法 note_type
        resp2 = client.post("/api/watchlist/600519/notes",
                            json={"note": "test", "note_type": "invalid"})
        assert resp2.status_code == 400

    def test_get_all_notes(self, client):
        """批量获取所有笔记"""
        client.post("/api/watchlist/600519")
        client.post("/api/watchlist/000792")
        client.post("/api/watchlist/600519/notes",
                    json={"note": "笔记1", "note_type": "weekly"})
        client.post("/api/watchlist/000792/notes",
                    json={"note": "笔记2", "note_type": "analysis"})
        resp = client.get("/api/watchlist/notes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["notes"]) == 2

    def test_add_note_nonexistent_stock(self, client):
        """不存在的股票仍可添加笔记（笔记独立于 watchlist 存在）"""
        resp = client.post("/api/watchlist/999999/notes",
                           json={"note": "test", "note_type": "weekly"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_get_full_data(self, client):
        """获取钉选股完整数据"""
        # 先插入 stock_snapshot 数据（watchlist add 依赖它）
        from src.models.database import db_conn
        from src.utils import now_cn
        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO stock_snapshot (code, name, current_price, pe, pb, roe, market_cap, snapshot_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("600519", "贵州茅台", 1500.0, 28.5, 9.2, 25.3, 2000.0, now_cn().strftime("%Y-%m-%d"))
            )
        client.post("/api/watchlist/600519")
        client.post("/api/watchlist/600519/notes",
                    json={"note": "测试笔记", "note_type": "weekly"})
        resp = client.get("/api/watchlist/600519/full")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "600519"
        assert data["name"] == "贵州茅台"
        assert "current_price" in data
        assert "pe" in data
        assert "ai_parsed" in data
        assert "analysis_history" in data
        assert "notes" in data
        assert len(data["notes"]) == 1

    def test_get_full_data_not_found(self, client):
        """不存在的股票 → 404"""
        resp = client.get("/api/watchlist/999999/full")
        assert resp.status_code == 404

    def test_add_note_with_model(self, client):
        """外部 AI 笔记带模型标识 → 写库可溯源"""
        client.post("/api/watchlist/600519")
        resp = client.post("/api/watchlist/600519/notes",
                           json={"note": "AI 分析", "note_type": "analysis",
                                 "model": "external-ai-v1"})
        assert resp.status_code == 200
        assert resp.json()["model"] == "external-ai-v1"
        notes = client.get("/api/watchlist/600519/notes").json()["notes"]
        assert notes[0]["model"] == "external-ai-v1"

    def test_add_note_without_model_backward_compat(self, client):
        """不带 model 旧调用仍可用（NULL 落库）"""
        client.post("/api/watchlist/600519")
        resp = client.post("/api/watchlist/600519/notes",
                           json={"note": "用户手记", "note_type": "user"})
        assert resp.status_code == 200
        assert resp.json()["model"] is None
        notes = client.get("/api/watchlist/600519/notes").json()["notes"]
        assert notes[0]["model"] is None

    def test_add_note_invalid_model(self, client):
        """model 非字符串 → 400"""
        client.post("/api/watchlist/600519")
        resp = client.post("/api/watchlist/600519/notes",
                           json={"note": "x", "note_type": "user",
                                 "model": 123})
        assert resp.status_code == 400

    def test_notes_model_column_migrated(self, client):
        """迁移守卫：watchlist_notes 含 model 列"""
        from src.models.database import db_conn
        with db_conn() as conn:
            cols = {r[1] for r in
                    conn.execute("PRAGMA table_info(watchlist_notes)").fetchall()}
        assert "model" in cols


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
