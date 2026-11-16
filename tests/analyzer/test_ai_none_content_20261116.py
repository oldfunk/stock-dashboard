"""推理模型把 token 预算全给 reasoning → content=None 的处理（2026-11-16）。

旧代码在 `not content` 分支的日志里又调 `len(content)` → TypeError，
被宽 except 吞成"异常(...): object of type 'NoneType' has no len()"，
掩盖了真实原因（max_tokens 不够）。现在安全记 0 字符 + finish_reason。
"""

import httpx

import src.analyzer.ai_analyzer as m


def test_none_content_does_not_raise_and_reports_finish_reason(monkeypatch):
    monkeypatch.setattr(m, "BACKOFF_SCHEDULE", [0] * 20)
    a = m.AiAnalyzer({"api_key": "k", "api_base": "http://x", "model": "mdl",
                      "rpm_limit": 0, "fallback": {"enabled": False}})
    monkeypatch.setattr(a, "_mark_model_dead", lambda *args, **kw: None)
    monkeypatch.setattr(a, "_resolve_model", lambda: "mdl")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"index": 0,
                                 "message": {"content": None},
                                 "finish_reason": "length"}]}

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: FakeResp())
    assert a._call_llm("hi") == (None, "mdl", None)
