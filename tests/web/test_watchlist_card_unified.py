"""观察池卡片与候选卡 UI 收敛测试（离线 Jinja 渲染，无需 TestClient）"""

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

TPL_DIR = str(Path(__file__).parent.parent.parent
              / "src" / "web" / "templates")


def _render(watchlist):
    env = Environment(loader=FileSystemLoader(TPL_DIR),
                      autoescape=select_autoescape(["html"]))
    return env.get_template("_watchlist_card.html").render(watchlist=watchlist)


def _full_item():
    return {
        "code": "600519", "name": "贵州茅台", "signal": "BUY", "score": 88.0,
        "model": "ling-3.0-flash-fin-free", "ai_confidence": "高",
        "review_count": 3, "current_price": 1500.0, "change_percent": 1.2,
        "pe": 22.0, "pb": 8.0, "roe": 30.0, "debt_ratio": 20.0,
        "revenue_growth": 15.0, "profit_growth": 18.0, "market_cap": 19000,
        "gross_margin": 91.0, "net_margin": 52.0, "ocf_per_share": 12.0,
        "_summary": {"data_years": 10, "roe_5y_avg": 29.0, "roe_10y_avg": 28.0,
                     "intcov_5y_avg": 99.0, "fcf_5y_sum": 1e11,
                     "fcf_positive_years_10": 10, "share_dilution_5y": 0.0},
        "reason": "ROE 三连",
        "score_detail_parsed": {
            "roe": {"raw": 30.0, "sub": 95, "weight": 0.3, "contribution": 28.5},
            "pe": {"raw": 22.0, "sub": 60, "weight": 0.2, "contribution": 12.0},
            "growth": {"raw": 15.0, "sub": 80, "weight": 0.2, "contribution": 16.0},
            "debt": {"raw": 20.0, "sub": 90, "weight": 0.15, "contribution": 13.5},
            "margin": {"raw": 91.0, "sub": 95, "weight": 0.15, "contribution": 14.25},
            "consistency_bonus": 2.0, "total": 86.25,
        },
        "moat_type": "品牌", "mgmt_score": "优秀", "iv_range": "1200-1800",
        "ai_failed": False, "ai_failure_reason": None,
        "ai_parsed": {
            "analysis": "生意极好",
            "investment_strategy": "长期持有",
            "reverse_thinking": "估值偏高",
            "moat_evaluation": [{"type": "品牌", "score": 5}],
            "management_score": {"capital_allocation": 8,
                                 "shareholder_friendliness": 9},
            "verdict": "通过：低估",
            "price_tiers": {"moderate": {"range": "1300-1600", "advice": "分批建仓"}},
            "intrinsic_value": {"base_case": 20000},
        },
        "trade_parsed": {"signal": "BUY", "confidence": "高",
                         "buy_zone": "1400-1500", "target_price": "1800",
                         "stop_loss": "1300", "take_profit": "1800"},
        "mirror_counts": None, "mirror_total": 0,
        "analysis_history": [{
            "analysis_date": "2026-09-16", "score": 86.0,
            "ai_analysis": '{"analysis": "好"}',
            "hist_analysis": "生意极好", "hist_strategy": "持有",
            "hist_trade": {"signal": "BUY", "confidence": "高",
                           "buy_zone": "1400-1500", "target_price": "1800",
                           "stop_loss": "1300"},
            "hist_mirror": "",
        }],
    }


def test_full_item_renders_unified_blocks():
    """全量数据：候选卡同款区块全部出现"""
    html = _render([_full_item()])
    assert "护城河: 品牌" in html          # 头部摘要
    assert "评分拆解" in html               # 评分块
    assert "ai-summary-row" in html        # AI 摘要行
    assert "ai-verdict-pass" in html       # 结论徽标
    assert "稳健区间" in html
    assert "BUY 置信度高" in html           # trade-guide
    assert "分层建议" in html
    assert "历史分析 (1次)" in html         # 时间线
    assert "池3周" in html                  # 池专属保留
    assert "AI 未分析" not in html


def test_failed_item_shows_badge_only():
    """AI 失败：徽标出现，AI 区块隐藏"""
    item = _full_item()
    item.update({"ai_parsed": None, "trade_parsed": None, "signal": None,
                 "ai_failed": True, "ai_failure_reason": "全部免费模型不可用",
                 "moat_type": None, "mgmt_score": None, "iv_range": None,
                 "score_detail_parsed": None, "analysis_history": [],
                 "model": None, "ai_confidence": None})
    html = _render([item])
    assert "AI 未分析" in html
    assert "全部免费模型不可用" in html
    assert "评分拆解" not in html
    assert '<div class="ai-summary">' not in html  # 样式块除外，只查渲染
    assert "稳健区间" not in html
    assert "池3周" in html


def test_empty_watchlist():
    """空池状态"""
    html = _render([])
    assert "观察池为空" in html
