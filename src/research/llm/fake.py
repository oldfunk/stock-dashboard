"""离线测试夹具（2026-10-26，R0-12）。

- ``FakeClock``：固定时钟（可注入 ``now_cn``/``today``）。
- ``FakeLLM``：假 LLM 客户端（预设响应队列，记录调用，不触网/不要 Key）。
- ``FakeToolGateway``：假工具网关（可注入结果）。

用途：CI/gate 与本地测试**不依赖外网/真实 Key**。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


class FakeClock:
    """固定时钟：``now_cn()`` 返回固定 ``datetime``。"""

    def __init__(self, now: str = "2026-10-26T12:00:00"):
        self._now = datetime.fromisoformat(now)

    def now_cn(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def isoformat(self) -> str:
        return self._now.isoformat()

    def strftime(self, fmt: str) -> str:
        return self._now.strftime(fmt)


class FakeLLM:
    """假 LLM：按顺序返回预设响应；记录调用；不触网。"""

    def __init__(self, responses: list[Any] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def complete(self, task: str, prompt: str, model_tier: str = "fast",
                 tools: list[str] | None = None, **kwargs) -> dict:
        self.calls.append({"task": task, "prompt": prompt,
                           "model_tier": model_tier, "tools": tools or []})
        if not self.responses:
            raise RuntimeError("FakeLLM 无预设响应")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FakeToolGateway:
    """假工具网关：按工具名返回预设结果。"""

    def __init__(self, results: dict[str, Any] | None = None):
        self.results = dict(results or {})
        self.calls: list[tuple[str, dict]] = []

    def call(self, task_id: str, tool: str, args: dict | None = None,
             **kwargs):
        self.calls.append((tool, args or {}))
        if tool not in self.results:
            raise KeyError(f"FakeToolGateway 未配置工具: {tool}")
        return self.results[tool]

    @property
    def tools_called(self) -> list[str]:
        return [t for t, _ in self.calls]
