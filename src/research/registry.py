"""Research OS 注册表（2026-10-26，R0-3）。

统一特征/假设/策略/提示词注册与校验：
- 必填字段缺失、非法状态、重复 ID/版本 → RegistryError，不落库。
- 领域对象/字典均可注册。
"""

from __future__ import annotations

from typing import Any, Iterable

from src.models.database import db_conn
from src.research.dao import ResearchDAO
from src.research.domain import HypothesisStatus, StrategyStatus


class RegistryError(ValueError):
    """注册校验失败。"""


def _data(obj: Any) -> dict[str, Any]:
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def _require(data: dict[str, Any], fields: Iterable[str], entity: str,
             nonempty: Iterable[str] = ()) -> None:
    """校验必填字段。

    - 缺失/空字符串一律拒绝；
    - ``nonempty`` 中的字段额外要求"非空容器/非零"（空 signal/regimes 视为未定义）。
    """
    missing = [
        f for f in fields
        if data.get(f) is None
        or (isinstance(data.get(f), str) and data.get(f) == "")
        or (f in nonempty and not data.get(f))
    ]
    if missing:
        raise RegistryError(f"{entity} 缺少必填字段: {', '.join(missing)}")


def _in(value: Any, allowed: Iterable[str], entity: str, field: str) -> None:
    if value not in allowed:
        raise RegistryError(f"{entity}.{field} 非法值: {value!r}")


class FeatureRegistry:
    REQUIRED = ("feature_id", "version", "name", "status")
    STATUS = ("draft", "active", "retired")

    def __init__(self, dao: ResearchDAO | None = None):
        self.dao = dao or ResearchDAO()

    def register(self, obj: Any) -> None:
        d = _data(obj)
        _require(d, self.REQUIRED, "feature")
        _in(d.get("status"), self.STATUS, "feature", "status")
        if self.get(d["feature_id"], d["version"]) is not None:
            raise RegistryError(
                f"feature 已存在: {d['feature_id']} v{d['version']}")
        self.dao.insert_feature_definition(d)

    def get(self, feature_id: str, version: int) -> dict | None:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM feature_definition WHERE feature_id=? AND version=?",
                (feature_id, version)).fetchone()
        return dict(row) if row else None


class HypothesisRegistry:
    REQUIRED = (
        "id", "version", "prior", "statement", "universe", "signal",
        "horizon_days", "benchmark", "metric", "threshold", "regimes",
        "costs", "kill_criteria", "status",
    )
    # 空 signal/regimes/kill_criteria 视为未定义（空 costs 合理：无费用假设）
    NONEMPTY = ("signal", "regimes", "kill_criteria")
    STATUS = tuple(s.value for s in HypothesisStatus)

    def __init__(self, dao: ResearchDAO | None = None):
        self.dao = dao or ResearchDAO()

    def register(self, obj: Any) -> None:
        d = _data(obj)
        _require(d, self.REQUIRED, "hypothesis", self.NONEMPTY)
        _in(d.get("status"), self.STATUS, "hypothesis", "status")
        if self.get(d["id"]) is not None:
            raise RegistryError(f"hypothesis 已存在: {d['id']}")
        self.dao.insert_hypothesis(d)

    def get(self, hypothesis_id: str) -> dict | None:
        return self.dao.get_hypothesis(hypothesis_id)


class StrategyRegistry:
    REQUIRED = ("id", "version", "spec", "status")
    NONEMPTY = ("spec",)
    STATUS = tuple(s.value for s in StrategyStatus)

    def __init__(self, dao: ResearchDAO | None = None):
        self.dao = dao or ResearchDAO()

    def register(self, obj: Any) -> None:
        d = _data(obj)
        _require(d, self.REQUIRED, "strategy", self.NONEMPTY)
        _in(d.get("status"), self.STATUS, "strategy", "status")
        if self.get(d["id"]) is not None:
            raise RegistryError(f"strategy 已存在: {d['id']}")
        self.dao.insert_strategy(d)

    def get(self, strategy_id: str) -> dict | None:
        return self.dao.get_strategy(strategy_id)


class PromptRegistry:
    REQUIRED = ("prompt_id", "version", "task", "template_hash", "status")
    STATUS = ("active", "retired")

    def __init__(self, dao: ResearchDAO | None = None):
        self.dao = dao or ResearchDAO()

    def register(self, obj: Any) -> None:
        d = _data(obj)
        _require(d, self.REQUIRED, "prompt")
        _in(d.get("status"), self.STATUS, "prompt", "status")
        if d.get("max_tokens") is not None and not isinstance(
                d.get("max_tokens"), int):
            raise RegistryError("prompt.max_tokens 必须为整数")
        if self.get(d["prompt_id"], d["version"]) is not None:
            raise RegistryError(
                f"prompt 已存在: {d['prompt_id']} v{d['version']}")
        self.dao.insert_prompt(d)

    def get(self, prompt_id: str, version: int) -> dict | None:
        return self.dao.get_prompt(prompt_id, version)


# 便捷入口：注册表集合
class ResearchRegistries:
    def __init__(self, dao: ResearchDAO | None = None):
        self.dao = dao or ResearchDAO()
        self.feature = FeatureRegistry(self.dao)
        self.hypothesis = HypothesisRegistry(self.dao)
        self.strategy = StrategyRegistry(self.dao)
        self.prompt = PromptRegistry(self.dao)
