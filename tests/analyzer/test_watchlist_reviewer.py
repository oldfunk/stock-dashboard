"""WatchlistReviewer 硬规则层单测

4 条硬规则各自触发条件 + 不触发情况。
"""

import pytest

from src.analyzer.watchlist_reviewer import (
    WatchlistReviewer,
    check_signal_avoid,
    check_roe_below,
    check_price_above_buyzone,
    check_roe_collapse,
)


def test_signal_avoid_triggered():
    """规则 1: Signal=AVOID → 强制调出"""
    stock = {"code": "002415", "name": "海康威视", "roe": 15.0}
    analysis = {"trade_strategy": {"signal": "AVOID"}}
    assert check_signal_avoid(stock, analysis) is True


def test_signal_avoid_not_triggered_when_buy():
    stock = {"code": "002415", "name": "海康威视"}
    analysis = {"trade_strategy": {"signal": "BUY"}}
    assert check_signal_avoid(stock, analysis) is False


def test_signal_avoid_not_triggered_when_no_analysis():
    stock = {"code": "002415", "name": "海康威视"}
    assert check_signal_avoid(stock, None) is False


def test_roe_below_triggered():
    """规则 2: ROE < 5% → 强制调出"""
    stock = {"code": "600000", "name": "某股", "roe": 3.5}
    analysis = {}
    assert check_roe_below(stock, analysis, threshold=5) is True


def test_roe_below_not_triggered():
    stock = {"code": "600519", "name": "贵州茅台", "roe": 30.0}
    analysis = {}
    assert check_roe_below(stock, analysis, threshold=5) is False


def test_roe_below_not_triggered_when_none():
    stock = {"code": "600000", "name": "某股", "roe": None}
    assert check_roe_below(stock, {}, threshold=5) is False


def test_price_above_buyzone_triggered():
    """规则 3: 当前价 > 买入区上限 +20% → 强制调出"""
    stock = {"code": "600519", "name": "贵州茅台", "current_price": 2000.0}
    analysis = {"trade_strategy": {"buy_zone": "1500-1700"}}
    # 买入区上限 1700, +20% = 2040, 当前 2000 < 2040, 不触发
    assert check_price_above_buyzone(stock, analysis, pct=20) is False

    stock2 = {"code": "600519", "name": "贵州茅台", "current_price": 2100.0}
    # 2100 > 2040, 触发
    assert check_price_above_buyzone(stock2, analysis, pct=20) is True


def test_price_above_buyzone_no_buyzone():
    """买入区字段缺失 → 不触发"""
    stock = {"code": "600519", "current_price": 5000.0}
    analysis = {"trade_strategy": {}}
    assert check_price_above_buyzone(stock, analysis, pct=20) is False


def test_price_above_buyzone_invalid_buyzone():
    """买入区格式异常 → 不触发（容错）"""
    stock = {"code": "600519", "current_price": 5000.0}
    analysis = {"trade_strategy": {"buy_zone": "未知"}}
    assert check_price_above_buyzone(stock, analysis, pct=20) is False


def test_roe_collapse_triggered():
    """规则 4: ROE 同比下降 > 10pp → 强制调出"""
    stock = {
        "code": "600000", "name": "某股",
        "roe": 8.0,
        "_history_roe": {"last_year": 20.0}  # 去年 20%, 今年 8%, 下降 12pp
    }
    analysis = {}
    assert check_roe_collapse(stock, analysis, pp=10) is True


def test_roe_collapse_not_triggered():
    stock = {
        "code": "600519", "name": "贵州茅台",
        "roe": 28.0,
        "_history_roe": {"last_year": 30.0}  # 下降 2pp, 未达 10pp
    }
    assert check_roe_collapse(stock, {}, pp=10) is False


def test_roe_collapse_no_history():
    """无历史 ROE → 不触发"""
    stock = {"code": "600519", "roe": 8.0, "_history_roe": None}
    assert check_roe_collapse(stock, {}, pp=10) is False


def test_apply_hard_rules_collects_all_triggers():
    """多个硬规则同时触发 → 全部收集"""
    reviewer = WatchlistReviewer({
        "ai_review": {"hard_rules": {
            "signal_avoid": True,
            "roe_below": 5,
            "price_above_buyzone_pct": 20,
            "roe_collapse_pp": 10,
        }}
    })
    current = [
        {"code": "002415", "name": "海康威视", "roe": 15.0,
         "current_price": 100.0, "_history_roe": {"last_year": 30.0}},
    ]
    analyses = {
        "002415": {"trade_strategy": {"signal": "AVOID", "buy_zone": "50-80"}},
    }
    # 002415: Signal=AVOID ✓, ROE=15 不触发, 价格=100 vs 80*1.2=96 触发, ROE 下降 15pp 触发
    forced_out = reviewer._apply_hard_rules(current, analyses)
    codes = {f['code'] for f in forced_out}
    assert "002415" in codes
    # 触发原因列表不为空
    reasons = next(f['reasons'] for f in forced_out if f['code'] == '002415')
    assert len(reasons) >= 2  # 至少触发 2 条
