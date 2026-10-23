"""个股详情页细节优化回归测试（2026-10-23）。

四项：
1. 快照条指标按好/中/差上红/金/绿（随红绿开关），与主页卡片同口径。
2. 空值占位统一为 --（不再出现 --% / --亿 这类"占位+单位"）。
3. 硬编码颜色主题化（grade-b 金、signal-hold、K线/SVG 灰、按钮白字）。
4. 间距线框统一（六关卡片去外框线改浅灰底色块；行分隔虚线改实线浅灰）。
"""

import pytest
from fastapi.testclient import TestClient

TPL_DIR = __import__("pathlib").Path(__file__).resolve().parents[2] / "src" / "web" / "templates"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "d.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _snap(**over):
    base = {
        "code": "000792", "name": "盐湖股份", "market": "A", "sector": "化工原料",
        "pe": 12.5, "pb": 2.1, "ps": 3.0, "market_cap": 980.0, "circulating_cap": 900.0,
        "roe": 18.3, "revenue": 100.0, "revenue_growth": 15.0, "profit": 20.0,
        "profit_growth": 20.0, "debt_ratio": 35.0, "dividend_yield": 2.1,
        "current_price": 18.42, "high_52w": 25.0, "low_52w": 12.0,
        "is_st": False, "list_date": "2000-01-01", "snapshot_date": "2026-10-21",
    }
    base.update(over)
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([base])


def test_snapshot_bar_metrics_colored(client):
    """PE<15=好、PB 2~3.5=中，应带 good/warn 类"""
    _snap()
    html = client.get("/stock/000792").text
    assert 'class="value good"' in html
    assert 'class="value warn"' in html
    assert ".sd-metric .value.good { color: var(--color-up); }" in html
    assert ".sd-metric .value.warn { color: var(--gold); }" in html
    assert ".sd-metric .value.bad { color: var(--color-down); }" in html


def test_snapshot_bar_empty_values_no_unit_suffix(client):
    """缺失指标显示 --，不带 %/亿 后缀"""
    _snap(roe=None, revenue_growth=None, profit_growth=None,
          debt_ratio=None, dividend_yield=None, market_cap=None)
    html = client.get("/stock/000792").text
    assert "--%" not in html
    assert "--亿" not in html


def test_detail_hardcoded_colors_themed(client):
    """硬编码颜色清零：grade-b 金、signal-hold、K线/SVG 灰、按钮白字"""
    _snap()
    html = client.get("/stock/000792").text
    assert "rgba(220, 170, 40" not in html
    assert "#b8860b" not in html
    assert "rgba(255,193,7" not in html
    assert 'stroke="#ccc"' not in html
    assert "color:#fff" not in html
    assert "--text-on-accent" in html


def test_checklist_uses_background_not_border(client):
    """六关卡片去外框线，改浅灰底色块"""
    _snap()
    html = client.get("/stock/000792").text
    assert ".sd-cl-item { background: var(--bg-tertiary);" in html
    assert ".sd-cl-item { border: 1px solid" not in html


def test_row_dividers_are_solid_light(client):
    """行分隔由虚线改实线浅灰（统一线框观感）"""
    _snap()
    html = client.get("/stock/000792").text
    assert "border-bottom: 1px dashed var(--divider-thin)" not in html
    assert "border-bottom: 1px solid var(--divider-thin)" in html


def test_watchlist_detail_snapshot_colored(client):
    """钉选股分析页（另一个股页）快照条同口径上色 + 空值占位"""
    _snap()
    html = client.get("/watchlist/000792").text
    assert 'class="value good"' in html
    assert ".wd-metric .value.good { color: var(--color-up); }" in html
    assert "--%" not in html
