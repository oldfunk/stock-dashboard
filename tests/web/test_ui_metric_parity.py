"""UI 指标单一事实源与表面一致性回归测试（2026-10-25）。

背景：同一组指标曾在 5 个表面各写一遍，标签/格式/阈值/缺值行为漂移
（股息率与连分年数多次"只修了一个表面"）。本测试锁定：

- 指标定义、格式、好中差阈值只在 `src/web/stock_metrics.py`
- 候选卡 / AI 观察池卡 / 手动钉选 API / 个股详情 / 观察池详情 必须同口径
- 缺失值统一 `--`，不得整项消失
- 静态资源版本号自动生成，不靠手写
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.web.stock_metrics import CARD_METRICS, metric_items


FULL = {
    "pe": 11.2, "pb": 1.87, "roe": 16.6, "debt_ratio": 11.6,
    "revenue_growth": 25.2, "profit_growth": 20.0, "market_cap": 86.0,
    "gross_margin": 42.7, "net_margin": 10.5, "ocf_per_share": 0.12,
    "dividend_yield": 3.517, "dividend_years": 7, "dividend_year": "2024",
    "code": "002215", "name": "诺普信", "score": 88.0, "watched": False,
    "current_price": 8.53, "change_percent": 0.59, "review_count": 1,
}

EMPTY = {"code": "000001", "name": "平安银行", "score": 60.0,
         "watched": False, "current_price": 10.0}


def test_registry_labels_are_canonical():
    labels = [m.label for m in CARD_METRICS]
    assert labels == ["PE", "PB", "ROE", "负债率", "营收增长", "利润增长",
                      "市值", "毛利率", "净利率", "OCF/股", "股息率", "连分年数"]


def test_negative_pe_is_bad_not_good():
    """亏损股 PE 为负：不能因为"小于 15"被标成 good（旧 UI 的真实错误）。"""
    items = {m["key"]: m for m in metric_items({"pe": -26.45, "pb": 3.58})}
    assert items["pe"]["text"] == "-26.5"
    assert items["pe"]["cls"] == "bad"
    assert items["pb"]["cls"] == "" or items["pb"]["cls"] in ("warn", "good")


def test_formatting_is_shared():
    items = {m["key"]: m for m in metric_items(FULL)}
    assert items["revenue_growth"]["text"] == "+25.2%"
    assert items["profit_growth"]["text"] == "+20.0%"
    assert items["market_cap"]["text"] == "86亿"
    assert items["dividend_yield"]["text"] == "3.52%"
    assert items["dividend_years"]["text"] == "7"
    assert items["dividend_yield"]["title"] == "股息率 = 2024 年报每股分红 / 现价"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "ui.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _render(tpl, **ctx):
    from src.web import routes
    return routes.templates.get_template(tpl).render(**ctx)


def test_card_surfaces_show_every_metric_with_same_text():
    stock = dict(FULL)
    stock["metrics"] = metric_items(stock)
    candidate = _render("_stock_list.html", stocks=[stock])
    pool = _render("_watchlist_card.html", watchlist=[stock])
    for m in stock["metrics"]:
        assert m["label"] in candidate, f"候选卡缺 {m['label']}"
        assert m["label"] in pool, f"观察池卡缺 {m['label']}"
        assert m["text"] in candidate, f"候选卡值不一致 {m['label']}={m['text']}"
        assert m["text"] in pool, f"观察池卡值不一致 {m['label']}={m['text']}"


def test_missing_values_are_placeholder_not_omitted():
    stock = dict(EMPTY)
    stock["metrics"] = metric_items(stock)
    candidate = _render("_stock_list.html", stocks=[stock])
    pool = _render("_watchlist_card.html", watchlist=[stock])
    for tpl in (candidate, pool):
        for m in stock["metrics"]:
            assert m["label"] in tpl, f"缺列: {m['label']}"
        assert "--" in tpl


def test_api_watchlist_uses_same_renderer(client):
    from src.models.database import StockSnapshotDAO, WatchlistDAO
    snap = dict(FULL)
    snap.update({"market": "A", "sector": "化工", "ps": 1.0,
                 "circulating_cap": 80.0, "revenue": 10.0, "profit": 2.0,
                 "high_52w": 10.0, "low_52w": 8.0, "is_st": False,
                 "list_date": "2010-01-01", "snapshot_date": "2026-10-23"})
    StockSnapshotDAO().save_batch([snap])
    WatchlistDAO().add("002215", "诺普信")
    item = client.get("/api/watchlist").json()[0]
    assert item["metrics_html"]
    for m in metric_items(item):
        assert m["label"] in item["metrics_html"]
        assert m["text"] in item["metrics_html"]


def test_detail_page_uses_same_metrics(client):
    from src.models.database import StockSnapshotDAO
    snap = dict(FULL)
    snap.update({"market": "A", "sector": "化工", "ps": 1.0,
                 "circulating_cap": 80.0, "revenue": 10.0, "profit": 2.0,
                 "high_52w": 10.0, "low_52w": 8.0, "is_st": False,
                 "list_date": "2010-01-01", "snapshot_date": "2026-10-23"})
    StockSnapshotDAO().save_batch([snap])
    html = client.get("/stock/002215").text
    for m in metric_items(snap):
        assert m["label"] in html
        assert m["text"] in html


def test_static_assets_use_automatic_version():
    from src.web.routes import static_url
    url = static_url("watchlist.js")
    assert re.fullmatch(r"/static/watchlist\.js\?v=\d+", url), url


def test_template_partial_is_the_only_metric_markup():
    """卡片模板里不得再出现任何手写指标标签（防回潮）。"""
    from pathlib import Path
    tpl_dir = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"
    for name in ("_stock_list.html", "_watchlist_card.html"):
        text = (tpl_dir / name).read_text(encoding="utf-8")
        for label in (">PE<", ">PB<", ">ROE<", ">毛利率<", ">股息率<", ">连分年数<"):
            assert label not in text, f"{name} 又手写了指标 {label}"
