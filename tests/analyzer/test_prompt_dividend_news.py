"""AI 分析 prompt 的股息/新闻注入单测（2026-10-21）。

hermetic：不调用 LLM、不读库；只截获 analyze_stock 组装的 prompt。
目的：新增占位符必须真的被填充，且缺数据时明说 N/A，不能静默变成 0。
"""

import src.analyzer.ai_analyzer as mod


def _stock(**kw):
    base = {
        "code": "600519",
        "name": "贵州茅台",
        "pe": 20.0,
        "pb": 5.0,
        "roe": 30.0,
        "revenue_growth": 10.0,
        "profit_growth": 12.0,
        "debt_ratio": 20.0,
        "market_cap": 1000.0,
        "gross_margin": 90.0,
        "ocf_per_share": 5.0,
        "reason": "测试",
        "roe_5y_avg": 30.0,
        "gross_margin_5y_avg": 90.0,
        "net_margin_5y_avg": 50.0,
        "ocf_5y_trend": 1,
        "intcov_5y_avg": 100.0,
        "fcf_5y_sum": 500e8,
        "share_dilution_5y": 0.0,
        "roic_5y_avg": 28.0,
        "roe_10y_avg": 28.0,
        "net_margin_10y_avg": 48.0,
        "roic_10y_avg": 26.0,
        "fcf_10y_sum": 900e8,
        "fcf_positive_years_10": 10,
        "share_dilution_10y": 0.0,
        "roe_volatility": 5.0,
        "roe_improvement": 1.0,
        "data_years": "2016-2025",
        "dividend_yield": 5.0,
        "payout_ratio": 50.0,
        "dividend_year": "2025",
    }
    base.update(kw)
    return base


def _capture_prompt(monkeypatch, stock):
    monkeypatch.setattr(mod, "build_news_context",
                        lambda code: "【新闻背景】测试新闻，利好")
    monkeypatch.setattr(mod, "_build_history_summary", lambda code: "")
    analyzer = mod.AiAnalyzer({
        "model": "test-model",
        "fallback": {"enabled": False},
    })
    analyzer.api_key = "test-key"
    captured = {}

    def fake_call(prompt):
        captured["prompt"] = prompt
        return None, None, None

    analyzer._call_llm = fake_call
    assert analyzer.analyze_stock(stock) is None
    return captured["prompt"]


class TestPromptInjection:
    def test_real_dividend_line_is_filled(self, monkeypatch):
        prompt = _capture_prompt(monkeypatch, _stock())
        assert "股息率：5.0% | 分红率：50.0%（2025年报）" in prompt
        assert "股息率：N/A" not in prompt

    def test_missing_dividend_is_explicit_na(self, monkeypatch):
        stock = _stock()
        for k in ("dividend_yield", "payout_ratio", "dividend_year"):
            stock.pop(k)
        prompt = _capture_prompt(monkeypatch, stock)
        assert "股息率：N/A（年报分红数据未覆盖）" in prompt
        assert "股息率：0" not in prompt

    def test_news_context_is_appended_and_guidance_present(self, monkeypatch):
        prompt = _capture_prompt(monkeypatch, _stock())
        assert "【新闻背景】测试新闻，利好" in prompt
        assert "新闻只作为事件风险提示" in prompt
