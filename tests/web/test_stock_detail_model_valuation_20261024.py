"""详情页展示系统模型估值（P0-6 可复现 FCFF）回归测试（2026-10-24）。

背景：模型估值此前只注入 AI prompt，UI 零展示（用户看不到）。
现在详情页新增"系统模型估值（可复现）"章节：三档 + 当前价 + 安全边际 + 假设；
无年报数据时 fail-closed 显示"数据不足"，不硬算、不占位。
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
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


def _snapshot():
    return {
        "code": "600519", "name": "贵州茅台", "market": "A", "sector": "白酒",
        "pe": 20.0, "pb": 8.0, "ps": 10.0, "market_cap": 15000.0,
        "circulating_cap": 15000.0, "roe": 30.0, "revenue": 1500.0,
        "revenue_growth": 10.0, "profit": 700.0, "profit_growth": 10.0,
        "debt_ratio": 20.0, "dividend_yield": 2.5, "current_price": 12.0,
        "high_52w": 20.0, "low_52w": 10.0, "is_st": False,
        "list_date": "2001-08-27", "snapshot_date": "2026-10-23",
    }


def test_detail_shows_model_valuation_tiers(client):
    """有年报数据：展示三档 + 安全边际 + 假设说明（与 AI prompt 同源）。"""
    from src.models.database import StockSnapshotDAO, FinancialHistoryDAO
    StockSnapshotDAO().save_batch([_snapshot()])
    FinancialHistoryDAO().batch_save([{
        "stock_code": "600519", "report_date": "2025-12-31",
        "fcf": 100.0, "total_shares": 10.0, "cash": 20.0,
        "interest_bearing_debt": 50.0,
    }])
    html = client.get("/stock/600519").text
    assert "系统模型估值" in html
    assert "保守估值" in html and "基准估值" in html and "乐观估值" in html
    assert "安全边际" in html
    assert "149.11" in html            # base_case 149.1128… → 149.11
    assert "92.0%" in html             # MOS = 1 − 12/149.11
    assert "FCFF" in html              # 方法
    assert "2025-12-31 年报" in html    # 数据来源
    assert "假设不同" in html           # 三档口径说明


def test_detail_model_valuation_fail_closed_without_financials(client):
    """无年报数据：章节仍在，但明确显示"数据不足"，不显示估值数字。"""
    from src.models.database import StockSnapshotDAO
    StockSnapshotDAO().save_batch([_snapshot()])
    html = client.get("/stock/600519").text
    assert "系统模型估值" in html
    assert "数据不足" in html
    assert "基准估值" not in html
