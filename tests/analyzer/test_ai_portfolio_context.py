"""E4：单股分析 prompt 必须带只读组合上下文，失败时不阻断。"""
from __future__ import annotations

from src.analyzer import ai_analyzer as mod


def _analyzer(monkeypatch):
    monkeypatch.setenv("STOCK_AI_API_KEY", "test-key")
    an = mod.AiAnalyzer({"api_base": "http://127.0.0.1:9/v1",
                         "model": "fake", "fallback": {"enabled": False}})
    return an


def test_prompt_includes_portfolio_context(monkeypatch):
    an = _analyzer(monkeypatch)
    monkeypatch.setattr(mod, "_portfolio_context_text",
                        lambda: "【组合上下文（只读）】持仓 000792 400 股")
    monkeypatch.setattr(mod, "_build_history_summary", lambda *a, **k: "")
    monkeypatch.setattr(mod, "build_news_context", lambda *a, **k: "")
    monkeypatch.setattr(mod, "_model_valuation_line", lambda *a, **k: "")
    captured = {}

    def fake_call(prompt):
        captured["prompt"] = prompt
        return None, None, None

    monkeypatch.setattr(an, "_call_llm", fake_call)
    an.analyze_stock({"code": "600519", "name": "贵州茅台"})
    assert "组合上下文" in captured["prompt"]
    assert "不得据此给交易建议" in captured["prompt"]


def test_prompt_survives_context_failure(monkeypatch):
    an = _analyzer(monkeypatch)
    monkeypatch.setattr(mod, "_portfolio_context_text",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(mod, "_build_history_summary", lambda *a, **k: "")
    monkeypatch.setattr(mod, "build_news_context", lambda *a, **k: "")
    monkeypatch.setattr(mod, "_model_valuation_line", lambda *a, **k: "")
    captured = {}
    def fake(p):
        captured["p"] = p
        return None, None, None

    monkeypatch.setattr(an, "_call_llm", fake)
    assert an.analyze_stock({"code": "600519", "name": "贵州茅台"}) is None
    assert "组合上下文" not in captured["p"]
