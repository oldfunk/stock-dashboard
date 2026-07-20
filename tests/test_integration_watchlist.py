"""端到端集成测试：scheduler → reviewer → DAO → 路由"""

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
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


def test_full_flow_empty_db(app_client):
    """空数据库：首页不崩溃，/journal 显示无笔记，/api/ai-watchlist 返回 []

    注：index.html 的 watchlist 区段被 {% if stocks %} 包裹，
    完全空 DB 时不渲染 "观察池为空" 文案；此处只验证 200 + 不报错。
    """
    resp = app_client.get("/")
    assert resp.status_code == 200

    resp = app_client.get("/journal")
    assert resp.status_code == 200
    assert "暂无笔记" in resp.text

    resp = app_client.get("/api/ai-watchlist")
    assert resp.status_code == 200
    assert resp.json() == []


def test_scheduler_triggers_review(monkeypatch, tmp_path):
    """模拟周六触发复盘"""
    from datetime import datetime
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()

    # mock now_cn 返回周六
    saturday = datetime(2026, 7, 25, 0, 5, 0)  # 2026-07-25 是周六
    import src.utils
    monkeypatch.setattr(src.utils, "now_cn", lambda: saturday)
    import src.scheduler
    monkeypatch.setattr(src.scheduler, "now_cn", lambda: saturday)
    import src.models.database
    # database.now_cn 也需要 mock（get_db_path 间接调用）
    monkeypatch.setattr(src.models.database, "now_cn", lambda: saturday)

    scheduler = src.scheduler.MarketScheduler()
    assert scheduler._last_review_date is None  # 初始为空

    # mock reviewer.review 返回成功
    call_count = [0]
    def fake_review(self, run_id):
        call_count[0] += 1
        return {"new_watchlist": ["600519"], "actions": {},
                "journal_date": "2026-07-25"}
    monkeypatch.setattr(
        "src.analyzer.watchlist_reviewer.WatchlistReviewer.review", fake_review
    )

    scheduler._check_weekly_review()
    # 异步线程启动，等待完成
    import time
    time.sleep(2)

    assert call_count[0] == 1
    # _last_review_date 应该在成功后设置
    assert scheduler._last_review_date is not None
