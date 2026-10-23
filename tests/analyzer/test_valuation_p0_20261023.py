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
    assert equity_per_share(1000.0, None, 10.0) == pytest.approx(100.0)
    assert net_debt({"cash": 20, "interest_bearing_debt": 50}) == 30
    assert net_debt({"cash": 20}) == -20
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
