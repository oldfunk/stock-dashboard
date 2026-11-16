"""GLM 调用参数：JSON 模式（2026-11-16 调优）。

实测：不开启 response_format=json_object 时，约半数输出带 markdown/前言，
json.loads 失败（靠兜底提取）；开启后稳定合法 JSON。
"""

import json

import httpx

import src.analyzer.ai_analyzer as m


def _payload(monkeypatch, cfg):
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"index": 0, "message": {
                "content": json.dumps({"analysis": "x", "trade_strategy": {
                    "signal": "HOLD", "confidence": "中"}}),
                "role": "assistant"}, "finish_reason": "stop"}],
                "usage": {}}

    def fake_post(self, url, **kw):
        seen.update(kw.get("json") or {})
        return FakeResp()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    a = m.AiAnalyzer(dict({"api_key": "k", "api_base": "http://x",
                           "model": "mdl", "rpm_limit": 0,
                           "fallback": {"enabled": False}}, **cfg))
    a._call_llm("hi")
    return seen


def test_json_mode_on_by_default(monkeypatch):
    p = _payload(monkeypatch, {})
    assert p.get("response_format") == {"type": "json_object"}


def test_json_mode_can_be_disabled(monkeypatch):
    p = _payload(monkeypatch, {"json_mode": False})
    assert "response_format" not in p


def test_max_tokens_uses_config(monkeypatch):
    p = _payload(monkeypatch, {"max_tokens": 16000})
    assert p["max_tokens"] == 16000
