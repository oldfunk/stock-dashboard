"""_enrich_stocks 分析摘要前置字段测试：moat_type / mgmt_score / iv_range"""

import json


def _run_enrich(monkeypatch, stocks):
    import src.web.routes as routes

    class FakeHist:
        def get_history(self, code, limit=20):
            return []

    class FakeFS:
        def get(self, code):
            return None

    class FakeWatch:
        def get_watched_codes(self):
            return set()

    monkeypatch.setattr(routes, "StockAnalysisHistoryDAO", lambda: FakeHist())
    monkeypatch.setattr(routes, "FinancialSummaryDAO", lambda: FakeFS())
    monkeypatch.setattr(routes, "WatchlistDAO", lambda: FakeWatch())
    routes._enrich_stocks(stocks)
    return stocks


def _ai_json():
    return json.dumps({
        "model": "test-model",
        "moat_evaluation": [
            {"type": "转换成本", "score": 4, "trend": "稳定", "evidence": "粘性高"},
            {"type": "网络效应", "score": 2, "trend": "稳定", "evidence": "弱"},
        ],
        "management_score": {
            "capital_allocation": "7",
            "shareholder_friendliness": "8",
            "summary": "管理层稳健",
        },
        "intrinsic_value": {
            "conservative": "500",
            "base_case": "650",
            "optimistic": "800",
            "margin_of_safety": "20%",
            "method": "Owner Earnings × 12倍",
        },
    }, ensure_ascii=False)


def test_enrich_summary_fields_present(monkeypatch):
    stocks = [{"code": "000001", "name": "平安银行", "ai_analysis": _ai_json(),
               "ai_trade_strategy": None, "score_detail": None,
               "ai_failed": 0, "ai_failure_reason": None}]
    _run_enrich(monkeypatch, stocks)
    s = stocks[0]
    assert s["moat_type"] == "转换成本"
    assert s["mgmt_score"]["summary"] == "管理层稳健"
    assert s["iv_range"]["base_case"] == "650"


def test_enrich_summary_fields_null_row(monkeypatch):
    """旧分析 NULL 行：三个字段全 None，模板必须不渲染摘要块"""
    stocks = [{"code": "000002", "name": "万科A", "ai_analysis": None,
               "ai_trade_strategy": None, "score_detail": None,
               "ai_failed": 0, "ai_failure_reason": None}]
    _run_enrich(monkeypatch, stocks)
    s = stocks[0]
    assert s["moat_type"] is None
    assert s["mgmt_score"] is None
    assert s["iv_range"] is None


def test_enrich_summary_fields_malformed(monkeypatch):
    """moat_evaluation 缺失/类型异常时不抛异常"""
    stocks = [{"code": "000003", "name": "测试", "ai_analysis": json.dumps({"analysis": "x"}),
               "ai_trade_strategy": None, "score_detail": None,
               "ai_failed": 0, "ai_failure_reason": None}]
    _run_enrich(monkeypatch, stocks)
    s = stocks[0]
    assert s["moat_type"] is None
    assert s["mgmt_score"] is None
    assert s["iv_range"] is None


def test_stock_list_template_renders_no_ai(monkeypatch):
    """_stock_list.html 不再内联 AI（2026-09-21 方向）：即使透传字段齐全，
    摘要块/分析区也不渲染；数据（代码/评分/指标）正常渲染"""
    import src.web.routes as routes

    full = {"code": "000001", "name": "平安银行", "score": 80, "model": "m",
            "ai_failed": False, "ai_failure_reason": None, "watched": False,
            "current_price": 10.0, "change_percent": 1.0, "pe": 5.0, "pb": 1.0,
            "roe": 12.0, "debt_ratio": 90.0, "revenue_growth": 5.0,
            "profit_growth": 5.0, "market_cap": 1000.0, "gross_margin": 30.0,
            "net_margin": 20.0, "ocf_per_share": 1.0, "_summary": None,
            "reason": None, "score_detail_parsed": None, "ai_parsed": None,
            "trade_parsed": None, "mirror_counts": None, "mirror_total": 0,
            "analysis_history": [],
            "moat_type": "转换成本",
            "mgmt_score": {"capital_allocation": "7", "shareholder_friendliness": "8",
                           "summary": "管理层稳健"},
            "iv_range": {"conservative": "500", "base_case": "650",
                         "optimistic": "800", "margin_of_safety": "20%",
                         "method": "Owner Earnings × 12倍"}}

    html = routes.templates.get_template("_stock_list.html").render(
        {"stocks": [full], "request": None})
    assert "护城河" not in html
    assert "ai-summary" not in html
    assert "trade-grid" not in html
    assert "history-bar" not in html
    assert "000001" in html
    assert "80" in html
