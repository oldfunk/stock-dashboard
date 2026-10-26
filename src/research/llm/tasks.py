"""LLM 任务注册表（2026-10-26，R0-8）。

职责：
- 注册研究助手任务：task_id/version/描述/模型档/工具白名单/输出 schema/token 预算。
- **未注册任务不可调用**；**schema 缺失拒绝**；状态非 active 拒绝。
- 任务与 R0-9 的 prompt 版本关联（``prompt_registry.prompt_id`` 指向 ``task_id``）。

原则：任务是白名单制——只有注册且 active、带 schema 的任务才能被 LLM 调用。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Iterable

from src.research.dao import ResearchDAO


class LLMError(ValueError):
    """任务未注册 / schema 缺失 / 状态不可用。"""


class ModelTier(str, Enum):
    FAST = "fast"
    DEEP = "deep"


REQUIRED = ("task_id", "version", "description", "model_tier", "tools",
            "output_schema", "max_tokens", "status")
STATUS = ("active", "retired")


@dataclass
class TaskSpec:
    task_id: str
    version: int
    description: str
    model_tier: str
    tools: list[str]
    output_schema: dict
    max_tokens: int
    status: str = "active"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


def _require(data: dict[str, Any], fields: Iterable[str] = REQUIRED,
             nonempty: Iterable[str] = ()) -> None:
    missing = [
        f for f in fields
        if data.get(f) is None
        or (isinstance(data.get(f), str) and data.get(f) == "")
        or (f in nonempty and not data.get(f))
    ]
    if missing:
        raise LLMError(f"task 缺少必填字段: {', '.join(missing)}")


class TaskRegistry:
    """任务注册表：注册校验 + 调用前断言。"""

    NONEMPTY = ("tools", "output_schema")

    def __init__(self, dao: ResearchDAO | None = None):
        self.dao = dao or ResearchDAO()

    def register(self, obj: Any) -> TaskSpec:
        d = obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)
        _require(d, nonempty=self.NONEMPTY)
        if d["model_tier"] not in tuple(t.value for t in ModelTier):
            raise LLMError(f"task.model_tier 非法值: {d['model_tier']!r}")
        if d["status"] not in STATUS:
            raise LLMError(f"task.status 非法值: {d['status']!r}")
        if not isinstance(d["max_tokens"], int) or d["max_tokens"] < 1:
            raise LLMError("task.max_tokens 必须为正整数")
        if self.get(d["task_id"], d["version"]) is not None:
            raise LLMError(f"task 已存在: {d['task_id']} v{d['version']}")
        spec = TaskSpec.from_dict(d)
        self.dao.insert_task(spec)
        return spec

    def get(self, task_id: str, version: int | None = None) -> TaskSpec | None:
        row = self.dao.get_task(task_id, version)
        return TaskSpec.from_dict(row) if row else None

    def require(self, task_id: str, version: int | None = None) -> TaskSpec:
        """未注册任务拒绝。"""
        spec = self.get(task_id, version)
        if spec is None:
            raise LLMError(f"未注册的 LLM 任务: {task_id}")
        return spec

    def assert_callable(self, task_id: str, version: int | None = None) -> TaskSpec:
        """调用前校验：已注册 + active + 有输出 schema + 有工具白名单。"""
        spec = self.require(task_id, version)
        if spec.status != "active":
            raise LLMError(f"任务不可用（status={spec.status}）: {task_id}")
        if not spec.output_schema:
            raise LLMError(f"任务缺少输出 schema，拒绝调用: {task_id}")
        if not spec.tools:
            raise LLMError(f"任务缺少工具白名单，拒绝调用: {task_id}")
        return spec

    def list_tasks(self, status: str | None = None) -> list[TaskSpec]:
        return [TaskSpec.from_dict(r) for r in self.dao.list_tasks(status)]

    def allowed_tools(self, task_id: str) -> list[str]:
        return self.assert_callable(task_id).tools
