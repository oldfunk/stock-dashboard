"""三个线上 UI bug 的回归测试（2026-10-22）。hermetic：tmp 库。

1. 候选卡「评分拆解」按钮必须 stopPropagation —— 否则展开同时冒泡跳转详情页。
2. /journal/{date} 必须 200 —— 10-20 新闻改动把 _news_dao 用进了没定义它的路由，
   历史笔记点开全 500，看起来像"笔记丢失"（数据完好）。
3. 红绿开关（data-color-scheme）必须全站跟随 —— stock_detail / watchlist_detail /
   outcomes 页面曾硬编码 cn 且不读 localStorage，切换后这些页不翻色。
"""

import json

import pytest
from fastapi.testclient import TestClient

SCORE_DETAIL = json.dumps({
    "total": 87.8,
    "roe": {"raw": 15.0, "sub": 80, "weight": 0.3, "contribution": 24.0},
    "pe": {"raw": 12.0, "sub": 70, "weight": 0.25, "contribution": 17.5},
    "growth": {"raw": 10.0, "sub": 60, "weight": 0.2, "contribution": 12.0},
    "debt": {"raw": 30.0, "sub": 75, "weight": 0.15, "contribution": 11.25},
    "margin": {"raw": 40.0, "sub": 78, "weight": 0.1, "contribution": 7.8},
    "consistency_bonus": 5.25,
})


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "u.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _snap(code="000792", name="盐湖股份"):
    return {
        "code": code, "name": name, "market": "A", "sector": "化工原料",
        "pe": 12.5, "pb": 2.1, "ps": 3.0, "market_cap": 980.0, "circulating_cap": 900.0,
        "roe": 18.3, "revenue": 100.0, "revenue_growth": 15.0, "profit": 20.0,
        "profit_growth": 20.0, "debt_ratio": 35.0, "dividend_yield": 2.1,
        "current_price": 18.42, "high_52w": 25.0, "low_52w": 12.0,
        "is_st": False, "list_date": "2000-01-01", "snapshot_date": "2026-10-21",
    }


def _save_screening(code="000792", name="盐湖股份"):
    from src.models.database import ScreeningResultDAO
    ScreeningResultDAO().save_batch([{
        "run_id": "20261021_153010", "run_date": "2026-10-21",
        "code": code, "name": name, "score": 87.8,
        "pe": 12.0, "pb": 2.1, "roe": 18.3,
        "gross_margin": 40.0, "net_margin": 20.0, "ocf_per_share": 1.5,
        "revenue_growth": 15.0, "profit_growth": 20.0, "debt_ratio": 35.0,
        "market_cap": 980.0, "reason": "测试",
        "score_detail": SCORE_DETAIL, "strategy_tags": None,
    }])


class TestScoreToggleNoNavigate:
    def test_index_score_toggle_stops_propagation(self, client):
        """候选卡评分拆解按钮带 stopPropagation（bug1：点击曾同时跳转详情页）"""
        from src.models.database import StockSnapshotDAO
        StockSnapshotDAO().save_batch([_snap()])
        _save_screening()

        resp = client.get("/")
        assert resp.status_code == 200
        assert 'onclick="event.stopPropagation(); toggleScoreDetail(\'000792\')"' in resp.text

    def test_watchcard_toggle_keeps_stop_propagation(self, client):
        """观察池卡同款按钮不得回退（原本就有 stopPropagation）
        同一只股同时出现在候选卡与观察池卡 → 两处按钮都必须带 stopPropagation。"""
        from src.models.database import StockSnapshotDAO
        StockSnapshotDAO().save_batch([_snap()])
        _save_screening()
        from src.models.ai_watchlist import AiWatchlistDAO
        AiWatchlistDAO().add("000792", "盐湖股份", "低估值", "中")

        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.text.count("event.stopPropagation(); toggleScoreDetail('000792')") == 2


class TestJournalByDateRoute:
    def test_journal_by_date_200(self, client):
        """历史笔记页曾 500（NameError: _news_dao）—— 数据没丢，是路由炸了"""
        from src.models.ai_watchlist import AiJournalDAO
        AiJournalDAO().save("2026-10-03", "r1", "本周复盘 - 调入诺普信",
                            "# 内容", None, None)
        AiJournalDAO().save("2026-10-17", "r2", "本周复盘 - 观察池稳定",
                            "# 内容2", None, None)

        resp = client.get("/journal/2026-10-03")
        assert resp.status_code == 200
        assert "本周复盘 - 调入诺普信" in resp.text
        # 侧栏归档仍列出全部历史
        assert "2026-10-17" in resp.text and "2026-10-03" in resp.text

    def test_journal_latest_still_ok(self, client):
        """默认页不回归"""
        from src.models.ai_watchlist import AiJournalDAO
        AiJournalDAO().save("2026-10-17", "r2", "本周复盘", "# 内容", None, None)
        resp = client.get("/journal")
        assert resp.status_code == 200
        assert "本周复盘" in resp.text


class TestColorSchemeFollowsToggle:
    """红绿开关偏好存 localStorage('stock-dashboard-color')，各页必须读回并覆盖 token"""

    def _assert_follows(self, html):
        assert 'data-color-scheme="us"' in html      # us 覆盖 CSS 块
        assert "stock-dashboard-color" in html       # 从 localStorage 恢复

    def test_stock_detail_follows(self, client):
        from src.models.database import StockSnapshotDAO
        StockSnapshotDAO().save_batch([_snap()])
        resp = client.get("/stock/000792")
        assert resp.status_code == 200
        self._assert_follows(resp.text)
        # K线颜色取自开关而非硬编码
        assert "klineSchemeColors" in resp.text

    def test_watchlist_detail_follows(self, client):
        from src.models.database import StockSnapshotDAO
        StockSnapshotDAO().save_batch([_snap()])
        resp = client.get("/watchlist/000792")
        assert resp.status_code == 200
        self._assert_follows(resp.text)

    def test_outcomes_page_follows(self, client):
        resp = client.get("/outcomes")
        assert resp.status_code == 200
        self._assert_follows(resp.text)

    def test_index_kline_uses_scheme(self, client):
        """首页指数 K线弹窗同样取开关色，且切换时对已开图表重刷"""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "klineSchemeColors" in resp.text
        assert "applyHollowCandleStyle(indexChart)" in resp.text
