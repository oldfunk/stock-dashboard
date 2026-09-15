"""_enrich_stocks 策略标签透传测试：strategy_tags（M2 前置）"""

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


def _base(code="000001"):
    return {"code": code, "name": "测试股", "ai_analysis": None,
            "ai_trade_strategy": None, "score_detail": None,
            "ai_failed": 0, "ai_failure_reason": None}


def test_strategy_tags_full(monkeypatch):
    stocks = [_base()]
    stocks[0]["strategy_tags"] = json.dumps(["growth", "dividend"], ensure_ascii=False)
    _run_enrich(monkeypatch, stocks)
    assert stocks[0]["strategy_tags"] == ["growth", "dividend"]


def test_strategy_tags_null_defaults_empty(monkeypatch):
    stocks = [_base()]
    stocks[0]["strategy_tags"] = None
    _run_enrich(monkeypatch, stocks)
    assert stocks[0]["strategy_tags"] == []


def test_strategy_tags_malformed_defaults_empty(monkeypatch):
    stocks = [_base()]
    stocks[0]["strategy_tags"] = "not-json{{{"
    _run_enrich(monkeypatch, stocks)
    assert stocks[0]["strategy_tags"] == []
