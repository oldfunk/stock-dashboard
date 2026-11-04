"""15-d 用例（2026-11-04，U8）：折现率口径必须随估值一起出现在 UI 与 prompt。

背景：三档折现率（0.10/0.09/0.08）是**股权要求回报**的量级，却被用来折 FCFF
（企业级现金流，教科书应对应 WACC）。母方裁定"口径并入 H-001 样本一起定，
本批先做文档警示"——所以本批**不改任何数值**，只把这句话做成单一来源
``valuation.DISCOUNT_BASIS_NOTE``，让详情页与 AI prompt 共用同一句，
并钉住"加了声明、数字没变"。
"""

import pytest

FCF_SEED = {
    "stock_code": "600519", "report_date": "2025-12-31",
    "fcf": 100.0, "total_shares": 10.0, "cash": 20.0,
    "interest_bearing_debt": 50.0,
}


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "d.db"))
    db_mod.init_database()
    from src.models.database import FinancialHistoryDAO
    FinancialHistoryDAO().batch_save([FCF_SEED])
    return db_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    db_path = str(tmp_path / "web.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.models.database import FinancialHistoryDAO, StockSnapshotDAO
    FinancialHistoryDAO().batch_save([FCF_SEED])
    StockSnapshotDAO().save_batch([{
        "code": "600519", "name": "贵州茅台", "market": "A", "sector": "白酒",
        "pe": 20.0, "pb": 8.0, "current_price": 12.0, "market_cap": 15000.0,
        "roe": 30.0, "is_st": False, "list_date": "2001-08-27",
        "snapshot_date": "2026-11-04",
    }])
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_note_names_the_rate_basis_direction_and_magnitude():
    from src.analyzer.valuation import DISCOUNT_BASIS_NOTE as note
    assert "股权要求回报" in note
    assert "WACC" in note
    assert "偏保守" in note, "方向必须写清（低估企业价值，不是算错方向）"
    assert "27" in note, "要带敏感性数字，不能只写一句定性的偏保守"


def test_valuation_payload_carries_the_note_with_the_numbers(db):
    """口径声明随估值结果一起出（单一来源，UI/prompt 各自不另写一份）。"""
    from src.analyzer.valuation import intrinsic_value_for_code
    val = intrinsic_value_for_code("600519", price=12.0)
    assert val["sufficient"] is True
    assert "股权要求回报" in val["discount_basis_note"]


def test_disclosure_does_not_change_any_number(db):
    """加了声明之后三档与安全边际一分不变（本件只声明、不改口径）。"""
    from src.analyzer.valuation import intrinsic_value_for_code
    val = intrinsic_value_for_code("600519", price=12.0)
    assert val["intrinsic_value"]["base_case"] == pytest.approx(149.1128, abs=1e-3)
    assert val["margin_of_safety"] == pytest.approx(92.0, abs=0.1)


def test_ai_prompt_line_includes_the_caveat(db):
    from src.analyzer.ai_analyzer import _model_valuation_line
    line = _model_valuation_line("600519", price=12.0)
    assert "系统模型估值" in line and "安全边际" in line
    assert "股权要求回报" in line, "模型看不到这句话，就会把三档当 WACC 绝对值校验"


def test_detail_page_renders_the_caveat(client):
    html = client.get("/stock/600519").text
    assert "系统模型估值" in html
    assert "股权要求回报" in html
    assert "149.11" in html, "数值渲染不受影响"
