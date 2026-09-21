"""列表卡片无 AI 内联测试（离线 Jinja 渲染，无需 TestClient）。

方向（2026-09-21）：AI 分析由外部 AI 通过 API 消费数据后输出，
候选卡 `_stock_list.html` 与观察池卡 `_watchlist_card.html` 只展示数据
（指标/评分拆解/入池原因/监控条件），不得内联渲染任何 AI 文本。
"""

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

TPL_DIR = str(Path(__file__).parent.parent.parent
              / "src" / "web" / "templates")

# AI 内联标记：任一出现在渲染输出中即违反方向
AI_MARKERS = [
    "ai-summary", "ai-section", "ai-tabs", "ai-tab", "ai-panel", "ai-text",
    "trade-grid", "trade-guide", "trade-cell", "trade-signal",
    "history-bar", "history-toggle", "history-item", "history-detail",
    "wl-ai", "wl-signal", "wl-tag-gold", "wl-tag-fail", "wl-ai-front",
    "stock-model", "stock-ai-fail",
    "护城河", "ai-verdict-", "稳健区间", "分层建议", "镜子测试",
    "买入区间", "AI 未分析", "待分析",
]

# 数据展示标记：必须保留
DATA_MARKERS_WATCHLIST = [
    "600519", "贵州茅台", "Score 88", "池3周", "评分拆解",
    "ROE 5y", "监控 →", "ROE 三连",
]
DATA_MARKERS_STOCKLIST = [
    "600519", "贵州茅台", "88", "评分拆解", "ROE:",
    "钉选", "ROE 三连",
]


def _render(tpl: str, **ctx) -> str:
    env = Environment(loader=FileSystemLoader(TPL_DIR),
                      autoescape=select_autoescape(["html"]))
    return env.get_template(tpl).render(**ctx)


def _full_watchlist_item() -> dict:
    """故意带全量 AI 字段：模板必须忽略它们，只渲染数据。"""
    return {
        "code": "600519", "name": "贵州茅台", "signal": "BUY", "score": 88.0,
        "model": "some-model", "ai_confidence": "高",
        "review_count": 3, "current_price": 1500.0, "change_percent": 1.2,
        "pe": 22.0, "pb": 8.0, "roe": 30.0, "debt_ratio": 20.0,
        "revenue_growth": 15.0, "profit_growth": 18.0, "market_cap": 19000,
        "gross_margin": 91.0, "net_margin": 52.0, "ocf_per_share": 12.0,
        "_summary": {"data_years": 10, "roe_5y_avg": 29.0, "roe_10y_avg": 28.0,
                     "intcov_5y_avg": 99.0, "fcf_5y_sum": 1e11,
                     "fcf_positive_years_10": 10, "share_dilution_5y": 0.0},
        "reason": "ROE 三连",
        "monitor_condition": "pe < 15",
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
        "ai_parsed": {"analysis": "生意极好", "verdict": "通过：低估",
                      "moat_evaluation": [{"type": "品牌", "score": 5}],
                      "management_score": {"capital_allocation": 8,
                                           "shareholder_friendliness": 9},
                      "price_tiers": {"moderate": {"range": "1300-1600",
                                                   "advice": "分批建仓"}},
                      "intrinsic_value": {"base_case": 20000},
                      "investment_strategy": "长期持有",
                      "reverse_thinking": "估值偏高"},
        "trade_parsed": {"signal": "BUY", "confidence": "高",
                         "buy_zone": "1400-1500", "target_price": "1800",
                         "stop_loss": "1300", "take_profit": "1800"},
        "mirror_counts": "转折词 3 句", "mirror_total": 3,
        "analysis_history": [{
            "analysis_date": "2026-09-16", "score": 86.0,
            "ai_analysis": '{"analysis": "好"}',
            "hist_analysis": "生意极好", "hist_strategy": "持有",
            "hist_trade": {"signal": "BUY", "confidence": "高"},
            "hist_mirror": "",
        }],
    }


def _full_stock_item() -> dict:
    item = _full_watchlist_item()
    item.update({"watched": False})
    return item


def test_watchlist_card_no_ai_inline():
    """观察池卡片：全量 AI 输入下无 AI 标记泄漏，数据都在"""
    html = _render("_watchlist_card.html",
                   watchlist=[_full_watchlist_item()])
    for m in AI_MARKERS:
        assert m not in html, f"AI 内联泄漏: {m}"
    for m in DATA_MARKERS_WATCHLIST:
        assert m in html, f"数据展示缺失: {m}"


def test_stock_list_no_ai_inline():
    """候选卡：全量 AI 输入下无 AI 标记泄漏，数据都在"""
    html = _render("_stock_list.html", stocks=[_full_stock_item()])
    for m in AI_MARKERS:
        assert m not in html, f"AI 内联泄漏: {m}"
    for m in DATA_MARKERS_STOCKLIST:
        assert m in html, f"数据展示缺失: {m}"


def test_empty_watchlist():
    """空池状态"""
    html = _render("_watchlist_card.html", watchlist=[])
    assert "观察池为空" in html
