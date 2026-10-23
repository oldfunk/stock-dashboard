"""候选股颜色表达统一走红绿开关的回归测试（2026-10-23）。

背景：候选卡 / 详情页 / 观察池详情页里编码正/负语义的颜色曾用 `--green` / `--red`
（或硬编码 rgba(30,160,90,…) / rgba(220,60,60,…)），切换 data-color-scheme="us"
时这些元素不翻色——红绿开关只覆盖了涨跌文字与 K线。

现统一映射到 `--color-up` / `--color-down`（含 `-bg`），开关覆盖候选股全部
颜色表达；中性状态（AVOID 灰、HOLD 金）与状态类 UI（流水线状态、AI 消息 ok/err）
不在此列。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TPL_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "templates"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "c.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _read(name):
    return (TPL_DIR / name).read_text(encoding="utf-8")


def test_index_candidate_metrics_follow_toggle():
    """候选卡指标 good/bad 必须走 color-up/down（原 --green/--red）"""
    css = _read("index.html")
    assert ".metric-cell .value.good { color: var(--color-up); }" in css
    assert ".metric-cell .value.bad { color: var(--color-down); }" in css
    assert ".metric-cell .value.warn { color: var(--gold); }" in css  # 中性档不动


def test_index_score_breakdown_follows_toggle():
    """评分拆解：进度条/贡献/总分走 color-up；AVOID 走 color-down"""
    css = _read("index.html")
    assert "background: var(--color-up);" in css
    assert ".sd-contrib { color: var(--color-up);" in css
    assert ".signal-avoid { color: var(--color-down);" in css


def test_stock_detail_positive_negative_follow_toggle():
    """详情页：评分贡献与信号/评级底色走 color-up/down(-bg)，不得残留硬编码 rgba"""
    css = _read("stock_detail.html")
    assert ".sd-sd-contrib { text-align: right; font-weight: 600; color: var(--color-up);" in css
    assert ".signal-buy { background: var(--color-up-bg); color: var(--color-up); }" in css
    assert "rgba(30, 160, 90" not in css
    assert "rgba(220, 60, 60" not in css
    assert "rgba(76,175,80" not in css
    assert "rgba(244,67,54" not in css


def test_watchlist_detail_positive_follows_toggle():
    """观察池详情页：在池状态/评分贡献/信号买走进 color-up(-bg)"""
    css = _read("watchlist_detail.html")
    assert ".wd-watchlist-status.in { background: var(--color-up-bg); color: var(--color-up); }" in css
    assert ".wd-sd-contrib { text-align: right; font-weight: 600; color: var(--color-up);" in css
    assert ".signal-buy { background: var(--color-up-bg); color: var(--color-up); }" in css


def test_candidate_card_price_change_is_inline_scheme(client):
    """渲染候选卡：涨跌百分比用内联 var(--color-up/down)，随开关翻色"""
    from src.models.database import StockSnapshotDAO
    from src.models.database import ScreeningResultDAO
    snap = {
        "code": "000792", "name": "盐湖股份", "market": "A", "sector": "化工原料",
        "pe": 12.5, "pb": 2.1, "ps": 3.0, "market_cap": 980.0, "circulating_cap": 900.0,
        "roe": 18.3, "revenue": 100.0, "revenue_growth": 15.0, "profit": 20.0,
        "profit_growth": 20.0, "debt_ratio": 35.0, "dividend_yield": 2.1,
        "current_price": 18.42, "high_52w": 25.0, "low_52w": 12.0,
        "is_st": False, "list_date": "2000-01-01", "snapshot_date": "2026-10-21",
    }
    StockSnapshotDAO().save_batch([snap])
    ScreeningResultDAO().save_batch([{
        "run_id": "20261021_153010", "run_date": "2026-10-21",
        "code": "000792", "name": "盐湖股份", "score": 87.8,
        "pe": 12.0, "pb": 2.1, "roe": 18.3, "gross_margin": 40.0, "net_margin": 20.0,
        "ocf_per_share": 1.5, "revenue_growth": 15.0, "profit_growth": 20.0,
        "debt_ratio": 35.0, "market_cap": 980.0, "reason": "测试",
        "score_detail": None, "strategy_tags": None,
    }])

    resp = client.get("/")
    assert resp.status_code == 200
    assert "color:var(--color-up)" in resp.text or "color: var(--color-up)" in resp.text
    assert "color:var(--color-down)" in resp.text or "color: var(--color-down)" in resp.text
