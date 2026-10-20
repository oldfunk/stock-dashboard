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
    assert ".news-ticker" in html
    assert "@keyframes news-scroll" in html


def test_ticker_has_no_manual_refresh_button(client):
    """用户要求：不要刷新按钮，后台持续拉取。"""
    html = client.get("/").text
    assert "news-refresh" not in html
    assert "newsRefresh" not in html
    js = open("src/web/static/news.js", encoding="utf-8").read()
    assert "newsRefresh" not in js
    assert "/api/news/ingest" not in js, "前端不该再触发手动抓取"


def test_ticker_speed_follows_content_length(client):
    """速度自适应：不能是固定秒数（条数少时飞一样快）。"""
    html = client.get("/").text
    assert "var(--news-speed" in html
    js = open("src/web/static/news.js", encoding="utf-8").read()
    assert "--news-speed" in js
    assert "Math.max(180" in js

def test_ticker_js_is_wired(client):
    """脚本要挂在首页上，且内容含渲染/刷新逻辑。"""
    html = client.get("/").text
    m = re.search(r'<script src="/static/news\.js[^"]*"></script>', html)
    assert m, "news.js 未挂载"
    js = open("src/web/static/news.js", encoding="utf-8").read()
    assert "/api/news/latest" in js
    assert "/api/news/latest" in js
    # 情绪三态都要有映射
    for w in ("利好", "利空"):
        assert w in js
    # 无缝滚动靠内容复制一份（html + html）实现 translateX(-50%) 对齐
    assert "+ html + html +" in js


def test_ticker_marks_market_items(client):
    """市场级要闻（code 为空）在滚动条里用「市场」标记区分。"""
    js = open("src/web/static/news.js", encoding="utf-8").read()
    assert "isMarket" in js and "news-code" in js
    assert ".news-code.market" in client.get("/").text
