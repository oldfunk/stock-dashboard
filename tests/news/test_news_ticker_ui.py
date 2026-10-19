"""首页新闻滚动条渲染门禁（2026-10-19）。"""
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_index_has_news_ticker(client):
    html = client.get("/").text
    assert 'id="newsTicker"' in html
    assert 'id="newsTrack"' in html
    assert '/static/news.js' in html
    # 滚动条样式在（类名唯一即可）
    assert ".news-ticker" in html
    assert "@keyframes news-scroll" in html


def test_ticker_js_is_wired(client):
    """脚本要挂在首页上，且内容含渲染/刷新逻辑。"""
    html = client.get("/").text
    m = re.search(r'<script src="/static/news\.js[^"]*"></script>', html)
    assert m, "news.js 未挂载"
    js = open("src/web/static/news.js", encoding="utf-8").read()
    assert "/api/news/latest" in js
    assert "/api/news/ingest" in js
    # 情绪三态都要有映射
    for w in ("利好", "利空"):
        assert w in js
    # 无缝滚动靠内容复制一份（html + html）实现 translateX(-50%) 对齐
    assert "+ html + html +" in js
