"""P0-6 可复现估值模型回归测试（2026-10-23）。

- FCFF 两阶段折现：固定输入 → 固定输出（可复现）
- 安全边际 = 1 − price / base 估值
- 数据不足返回 None（不硬算）
"""

import pytest

from src.analyzer.valuation import (
    dcf_value, intrinsic_value, equity_per_share, net_debt,
)


def test_dcf_known_values():
    assert dcf_value(100, 5, 0.0, 0.0, 0.0, 0.10) == pytest.approx(1000.0, rel=1e-9)
    assert dcf_value(100, 5, 0.03, 0.02, 0.02, 0.09) == pytest.approx(1521.1288141027642, rel=1e-9)
    assert dcf_value(100, 5, 0.06, 0.04, 0.03, 0.08) == pytest.approx(2433.6689397887594, rel=1e-9)


def test_dcf_boundaries():
    assert dcf_value(None, 5, 0.03, 0.02, 0.02, 0.09) is None
    assert dcf_value(0, 5, 0.03, 0.02, 0.02, 0.09) is None
    assert dcf_value(100, 5, 0.03, 0.02, 0.10, 0.09) is None  # r <= g_term
    assert dcf_value(100, 5, 0.03, 0.02, 0.02, None) is None


def test_equity_per_share_and_net_debt():
    assert equity_per_share(1000.0, 30.0, 10.0) == pytest.approx(97.0)
    assert equity_per_share(None, 30.0, 10.0) is None
    # 净负债未知不能当 0（否则估值被系统性高估）
    assert equity_per_share(1000.0, None, 10.0) is None
    assert net_debt({"cash": 20, "interest_bearing_debt": 50}) == 30
    # 任一侧缺失 → 未知，不是 0
    assert net_debt({"cash": 20}) is None
    assert net_debt({"interest_bearing_debt": 50}) is None
    assert net_debt({}) is None


def test_intrinsic_value_and_margin_of_safety():
    fin = {"fcf": 100, "total_shares": 10, "cash": 20, "interest_bearing_debt": 50}
    out = intrinsic_value(fin, price=12.0)
    assert out["net_debt"] == 30
    assert out["intrinsic_value"]["conservative"] == pytest.approx(97.0, rel=1e-9)
    assert out["intrinsic_value"]["base_case"] == pytest.approx(149.11288141027643, rel=1e-9)
    assert out["margin_of_safety"] == 92.0  # 1 − 12/149.11


def test_intrinsic_insufficient_data():
    out = intrinsic_value({"fcf": None, "total_shares": None}, price=10.0)
    assert out["intrinsic_value"]["base_case"] is None
    assert out["margin_of_safety"] is None
    assert out["sufficient"] is False
    assert out["insufficient_reason"] == "missing_fcf"
    assert out["insufficient_text"]


def test_intrinsic_negative_fcf_fail_closed():
    """FCF <= 0：三档全空且 sufficient=False（旧实现会误报 sufficient=True）。"""
    out = intrinsic_value(
        {"fcf": -1.0, "total_shares": 10.0, "cash": 20.0,
         "interest_bearing_debt": 50.0}, price=10.0)
    assert out["sufficient"] is False
    assert out["insufficient_reason"] == "negative_fcf"
    assert all(v is None for v in out["intrinsic_value"].values())
    assert out["margin_of_safety"] is None


def test_intrinsic_missing_net_debt_fail_closed():
    """净负债输入缺失：不得把未知当 0 算出估值。"""
    for fin in (
        {"fcf": 100.0, "total_shares": 10.0, "cash": None,
         "interest_bearing_debt": 50.0},          # 缺货币资金
        {"fcf": 100.0, "total_shares": 10.0, "cash": 20.0,
         "interest_bearing_debt": None},          # 缺有息负债
    ):
        out = intrinsic_value(fin, price=10.0)
        assert out["sufficient"] is False
        assert out["insufficient_reason"] == "missing_net_debt"
        assert out["net_debt"] is None
        assert all(v is None for v in out["intrinsic_value"].values())


def test_intrinsic_missing_shares_fail_closed():
    out = intrinsic_value(
        {"fcf": 100.0, "total_shares": None, "cash": 20.0,
         "interest_bearing_debt": 50.0}, price=10.0)
    assert out["sufficient"] is False
    assert out["insufficient_reason"] == "missing_shares"


def test_model_valuation_line_refuses_to_invent(tmp_path, monkeypatch):
    """输入不足时 prompt 明确写"不可用 + 原因"，禁止模型自编 DCF 数字。"""
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "n.db"))
    db_mod.init_database()
    from src.models.database import FinancialHistoryDAO
    FinancialHistoryDAO().batch_save([{
        "stock_code": "600519", "report_date": "2025-12-31",
        "fcf": -1.0, "total_shares": 10.0, "cash": 20.0,
        "interest_bearing_debt": 50.0,
    }])
    from src.analyzer.ai_analyzer import _model_valuation_line
    line = _model_valuation_line("600519", price=12.0)
    assert "不可用" in line
    assert "自由现金流为负" in line
    assert "不要自行编造" in line
    assert "安全边际" not in line


def test_model_valuation_line_injected(tmp_path, monkeypatch):
    """系统模型估值行：有数据时给 LLM 校验锚，无数据时空串（不阻断）。"""
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "m.db"))
    db_mod.init_database()
    from src.models.database import FinancialHistoryDAO
    FinancialHistoryDAO().batch_save([{
        "stock_code": "600519", "report_date": "2025-12-31",
        "fcf": 100.0, "total_shares": 10.0, "cash": 20.0,
        "interest_bearing_debt": 50.0,
    }])
    from src.analyzer.ai_analyzer import _model_valuation_line
    line = _model_valuation_line("600519", price=12.0)
    assert "系统模型估值" in line
    assert "安全边际" in line
    assert _model_valuation_line("999999", price=12.0) == ""


def test_intrinsic_value_for_code_fail_closed(tmp_path, monkeypatch):
    """统一入口：有年报 → 三档 + 安全边际 + 假设；无年报 → None（fail-closed）。"""
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "v.db"))
    db_mod.init_database()
    from src.models.database import FinancialHistoryDAO
    from src.analyzer.valuation import intrinsic_value_for_code
    # 无数据：不硬算
    assert intrinsic_value_for_code("000001", price=10.0) is None
    FinancialHistoryDAO().batch_save([{
        "stock_code": "600519", "report_date": "2025-12-31",
        "fcf": 100.0, "total_shares": 10.0, "cash": 20.0,
        "interest_bearing_debt": 50.0,
    }])
    val = intrinsic_value_for_code("600519", price=12.0)
    assert val is not None and val["sufficient"] is True
    assert val["report_date"] == "2025-12-31"
    assert val["price"] == 12.0
    assert val["intrinsic_value"]["base_case"] == pytest.approx(149.11288141027643, rel=1e-9)
    assert val["margin_of_safety"] == 92.0
    # 三档假设随结果返回（UI 展示同一模型的可审计依据）
    assert val["assumptions"]["base_case"]["discount"] == 0.09
    assert val["assumptions"]["conservative"]["g1"] == 0.0
