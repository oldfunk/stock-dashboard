"""AI 观察池 + 投资笔记路由测试"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个测试用独立临时数据库"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    # 避免启动调度器线程
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_api_ai_watchlist_empty(client):
    """空观察池"""
    resp = client.get("/api/ai-watchlist")
    assert resp.status_code == 200
    data = resp.json()
    assert data == []


def test_api_ai_watchlist_with_data(client):
    from src.models.ai_watchlist import AiWatchlistDAO
    AiWatchlistDAO().add("600519", "贵州茅台", "ROE 30%+", "高")
    AiWatchlistDAO().add("000792", "盐湖股份", "低估值", "中")

    resp = client.get("/api/ai-watchlist")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    codes = {item['code'] for item in data}
    assert codes == {"600519", "000792"}


def test_api_ai_watchlist_history(client):
    from src.models.ai_watchlist import AiWatchlistHistoryDAO
    dao = AiWatchlistHistoryDAO()
    dao.append("600519", "贵州茅台", "add", "新进", "r1", "2026-07-20")
    dao.append("002415", "海康威视", "remove", "AVOID", "r1", "2026-07-20")

    resp = client.get("/api/ai-watchlist/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_journal_page_no_data(client):
    """无笔记时 /journal 返回空状态"""
    resp = client.get("/journal")
    assert resp.status_code == 200
    assert "暂无笔记" in resp.text or "empty" in resp.text.lower()


def test_journal_page_with_data(client):
    from src.models.ai_watchlist import AiJournalDAO
    AiJournalDAO().save(
        "2026-07-20", "r1", "本周复盘",
        "# 本周复盘\n市场震荡", None, None
    )
    resp = client.get("/journal")
    assert resp.status_code == 200
    assert "本周复盘" in resp.text


def test_api_journal_latest(client):
    from src.models.ai_watchlist import AiJournalDAO
    AiJournalDAO().save(
        "2026-07-20", "r1", "本周复盘",
        "# 内容", None, None
    )
    resp = client.get("/api/journal/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data['title'] == "本周复盘"
    assert data['journal_date'] == "2026-07-20"


def test_api_journal_latest_404(client):
    resp = client.get("/api/journal/latest")
    assert resp.status_code == 404


def test_api_journal_by_date(client):
    from src.models.ai_watchlist import AiJournalDAO
    AiJournalDAO().save(
        "2026-07-20", "r1", "标题1",
        "# 内容1", None, None
    )
    resp = client.get("/api/journal/2026-07-20")
    assert resp.status_code == 200
    assert resp.json()['title'] == "标题1"

    resp = client.get("/api/journal/2020-01-01")
    assert resp.status_code == 404


def test_api_journal_drift_reentry_and_flip(client):
    """B5 drift：打脸回归 + 跨轮 Signal/verdict 翻转"""
    import json
    from src.models.ai_watchlist import AiJournalDAO
    from src.models.database import ScreeningResultDAO
    prev_actions = json.dumps({
        'add': [], 'remove': [{'code': '600519', 'reason': '贵'}],
        'keep': [], 'model': 'm1',
        'details': {'add': [], 'remove': [{'code': '600519', 'reason': '贵'}],
                    'keep': []}})
    cur_actions = json.dumps({
        'add': [{'code': '600519', 'reason': '回来'}], 'remove': [],
        'keep': [], 'model': 'm1',
        'details': {'add': [{'code': '600519', 'reason': '回来'}],
                    'remove': [], 'keep': []}})
    AiJournalDAO().save(
        "2026-07-20", "r1", "标题1", "# 内容1", None, prev_actions)
    AiJournalDAO().save(
        "2026-07-27", "r2", "标题2", "# 内容2", None, cur_actions)
    base = {"code": "600519", "name": "贵州茅台", "score": 85,
            "pe": 20, "pb": 5, "roe": 25, "market_cap": 2000,
            "reason": "测试"}
    ScreeningResultDAO().save_batch([
        dict(base, run_id="ro", run_date="2026-07-20"),
        dict(base, run_id="rn", run_date="2026-07-27"),
    ])
    ScreeningResultDAO().update_ai_analysis(
        "ro", "600519", json.dumps({"verdict": "通过：低估"}), "{}",
        json.dumps({"signal": "BUY"}))
    ScreeningResultDAO().update_ai_analysis(
        "rn", "600519", json.dumps({"verdict": "灰色地带：看不清"}), "{}",
        json.dumps({"signal": "HOLD"}))
    resp = client.get("/api/journal/2026-07-27/conflicts")
    assert resp.status_code == 200
    types = {c['type'] for c in resp.json()['conflicts']}
    assert 're_entry' in types
    assert 'signal_flip' in types
    assert 'verdict_flip' in types


def test_api_journal_list(client):
    from src.models.ai_watchlist import AiJournalDAO
    for i in range(3):
        AiJournalDAO().save(
            f"2026-07-{i+1:02d}", f"r{i}", f"第{i}周", "# C", None, None
        )
    resp = client.get("/api/journal/list")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert data[0]['journal_date'] == "2026-07-03"  # 最新在前
