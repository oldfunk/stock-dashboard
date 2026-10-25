"""手动钉选卡指标上色回归测试（2026-10-23）。

需求：手动钉选卡（钉选股票视图）的指标值与候选卡同口径上色——
好/中/差 → 红/金/绿（随红绿涨跌开关），并补毛利率/净利率/OCF 让"差"档绿色出现。

- /api/watchlist 必须从 screening_result 补全 gross_margin / net_margin / ocf_per_share
  （这些列只在 screening_result，不在 stock_snapshot）。
- watchlist.js 渲染时必须给指标值带 good/warn/bad 类。
"""

import pytest
from fastapi.testclient import TestClient

TPL_DIR = __import__("pathlib").Path(__file__).resolve().parents[2] / "src" / "web"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "w.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _seed(client):
    from src.models.database import StockSnapshotDAO, ScreeningResultDAO, WatchlistDAO
    StockSnapshotDAO().save_batch([{
        "code": "000792", "name": "盐湖股份", "market": "A", "sector": "化工原料",
        "pe": 25.0, "pb": 4.0, "roe": 5.0, "debt_ratio": 55.0,
        "revenue_growth": 3.0, "profit_growth": -10.0, "market_cap": 980.0,
        "current_price": 18.42, "snapshot_date": "2026-10-21",
        "dividend_yield": 3.5, "dividend_years": 7, "dividend_year": "2024",
    }])
    ScreeningResultDAO().save_batch([{
        "run_id": "20261021_153010", "run_date": "2026-10-21",
        "code": "000792", "name": "盐湖股份", "score": 60.0,
        "pe": 25.0, "pb": 4.0, "roe": 5.0, "debt_ratio": 55.0,
        "revenue_growth": 3.0, "profit_growth": -10.0, "market_cap": 980.0,
        "gross_margin": 10.0, "net_margin": 2.0, "ocf_per_share": -0.5,
        "reason": "测试",
    }])
    WatchlistDAO().add("000792", "盐湖股份")


def test_api_watchlist_enriches_margin_metrics(client):
    """/api/watchlist 补全毛利率/净利率/OCF（来自 screening_result）"""
    _seed(client)
    resp = client.get("/api/watchlist")
    assert resp.status_code == 200
    item = resp.json()[0]
    assert item["code"] == "000792"
    assert item["gross_margin"] == 10.0
    assert item["net_margin"] == 2.0
    assert item["ocf_per_share"] == -0.5
    # 股息三字段：钉选卡直接读这三个（year 用于 title 标注分红年度）
    assert item["dividend_yield"] == 3.5
    assert item["dividend_years"] == 7
    assert item["dividend_year"] == "2024"
    assert "股息率" in item["metrics_html"]
    assert "3.50%" in item["metrics_html"]


def test_api_watchlist_falls_back_to_snapshot(client):
    """无 screening_result 时保留 stock_snapshot 的指标（不报错、不丢字段）"""
    from src.models.database import StockSnapshotDAO, WatchlistDAO
    StockSnapshotDAO().save_batch([{
        "code": "000792", "name": "盐湖股份", "market": "A", "sector": "化工原料",
        "pe": 12.0, "pb": 1.5, "roe": 18.0, "debt_ratio": 30.0,
        "revenue_growth": 15.0, "profit_growth": 20.0, "market_cap": 500.0,
        "current_price": 10.0, "snapshot_date": "2026-10-21",
    }])
    WatchlistDAO().add("000792", "盐湖股份")
    resp = client.get("/api/watchlist")
    assert resp.status_code == 200
    item = resp.json()[0]
    assert item["pe"] == 12.0
    assert item["roe"] == 18.0
    assert item.get("gross_margin") is None  # snapshot 无此列，保持 None


def test_watchlist_js_renders_metric_color_classes():
    """watchlist.js 指标值必须带 good/warn/bad 类（与候选卡同口径）"""
    js = (TPL_DIR / "static" / "watchlist.js").read_text(encoding="utf-8")
    assert "s.gross_margin >= 30 ? 'good' : (s.gross_margin >= 15 ? 'warn' : 'bad')" in js
    assert "s.net_margin >= 15 ? 'good' : (s.net_margin >= 5 ? 'warn' : 'bad')" in js
    assert "s.ocf_per_share > 0 ? 'good' : 'bad'" in js
    assert "s.roe >= 15 ? 'good' : 'warn'" in js
    assert "s.pe < 15 ? 'good' : (s.pe < 20 ? 'warn' : '')" in js


def test_watchlist_js_uses_server_rendered_metrics():
    """钉选卡指标由服务端统一渲染；JS 不再手写标签/阈值（防再次漂移）。"""
    js = (TPL_DIR / "static" / "watchlist.js").read_text(encoding="utf-8")
    assert "s.metrics_html" in js
    assert "s.dividend_yield" not in js
    assert "s.dividend_years" not in js
    from src.web.stock_metrics import CARD_METRICS
    labels = [m.label for m in CARD_METRICS]
    assert "股息率" in labels and "连分年数" in labels
