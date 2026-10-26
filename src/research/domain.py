"""Research OS 领域对象与状态机（2026-10-26，R0-1）。

原则：
- ID 可读、可排序、可解析；前缀固定（H/E/S/D/MS/A/DL）。
- 状态机显式：非法迁移直接抛 DomainError。
- 领域对象可序列化（to_dict/from_dict），供 DAO 落库与 API 使用。
- 不依赖 DB；ID 序列由注册表/DAO 分配（本模块只提供格式与校验）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Any

ID_PATTERN = re.compile(
    r"^(?P<prefix>[A-Z]{1,3})-(?P<date>\d{8})-(?P<seq>\d{3,})$")

PREFIXES = {
    "H": "hypothesis",
    "E": "experiment",
    "S": "strategy",
    "D": "deployment",
    "MS": "market_state",
    "A": "attribution",
    "DL": "decision",
}


class DomainError(ValueError):
    """领域对象校验失败（非法状态迁移、非法 ID 等）。"""


class HypothesisStatus(str, Enum):
    DRAFT = "draft"
    LOCKED = "locked"
    TESTING = "testing"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    RETIRED = "retired"


class ExperimentStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    INVALID = "invalid"


class StrategyStatus(str, Enum):
    DRAFT = "draft"
    PAPER = "paper"
    RETIRED = "retired"


class DeploymentStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"


class DecisionStatus(str, Enum):
    OPEN = "open"
    REVIEWED = "reviewed"
    CLOSED = "closed"


# 合法状态迁移（有向）
HYPOTHESIS_TRANSITIONS = {
    HypothesisStatus.DRAFT: {HypothesisStatus.LOCKED, HypothesisStatus.RETIRED},
    HypothesisStatus.LOCKED: {HypothesisStatus.TESTING, HypothesisStatus.RETIRED},
    HypothesisStatus.TESTING: {
        HypothesisStatus.SUPPORTED, HypothesisStatus.REJECTED,
        HypothesisStatus.INCONCLUSIVE, HypothesisStatus.RETIRED},
    HypothesisStatus.SUPPORTED: {HypothesisStatus.RETIRED},
    HypothesisStatus.REJECTED: set(),
    HypothesisStatus.INCONCLUSIVE: {HypothesisStatus.TESTING},
    HypothesisStatus.RETIRED: set(),
}

EXPERIMENT_TRANSITIONS = {
    ExperimentStatus.PLANNED: {ExperimentStatus.RUNNING, ExperimentStatus.FAILED},
    ExperimentStatus.RUNNING: {
        ExperimentStatus.DONE, ExperimentStatus.FAILED,
        ExperimentStatus.INVALID},
    ExperimentStatus.DONE: set(),
    ExperimentStatus.FAILED: {ExperimentStatus.PLANNED},
    ExperimentStatus.INVALID: {ExperimentStatus.PLANNED},
}

STRATEGY_TRANSITIONS = {
    StrategyStatus.DRAFT: {StrategyStatus.PAPER, StrategyStatus.RETIRED},
    StrategyStatus.PAPER: {StrategyStatus.RETIRED},
    StrategyStatus.RETIRED: set(),
}

DEPLOYMENT_TRANSITIONS = {
    DeploymentStatus.RUNNING: {DeploymentStatus.STOPPED},
    DeploymentStatus.STOPPED: set(),
}

DECISION_TRANSITIONS = {
    DecisionStatus.OPEN: {DecisionStatus.REVIEWED, DecisionStatus.CLOSED},
    DecisionStatus.REVIEWED: {DecisionStatus.CLOSED, DecisionStatus.OPEN},
    DecisionStatus.CLOSED: set(),
}

TRANSITIONS = {
    "hypothesis": HYPOTHESIS_TRANSITIONS,
    "experiment": EXPERIMENT_TRANSITIONS,
    "strategy": STRATEGY_TRANSITIONS,
    "deployment": DEPLOYMENT_TRANSITIONS,
    "decision": DECISION_TRANSITIONS,
}


def format_id(prefix: str, day: date | str, seq: int) -> str:
    """生成可读 ID，如 H-20261026-001。"""
    if prefix not in PREFIXES:
        raise DomainError(f"未知 ID 前缀: {prefix}")
    if seq < 1:
        raise DomainError("seq 必须 >= 1")
    day_str = day if isinstance(day, str) else day.strftime("%Y%m%d")
    return f"{prefix}-{day_str}-{seq:03d}"


def parse_id(value: str) -> tuple[str, str, int]:
    """解析 ID；非法格式抛 DomainError。"""
    match = ID_PATTERN.match(value or "")
    if not match:
        raise DomainError(f"非法 ID: {value!r}")
    prefix = match.group("prefix")
    if prefix not in PREFIXES:
        raise DomainError(f"未知 ID 前缀: {prefix}")
    return prefix, match.group("date"), int(match.group("seq"))


def next_id(prefix: str, existing: list[str], day: date | str,
            start: int = 1) -> str:
    """在已有 ID 列表中生成下一个不冲突的 ID。"""
    day_str = day if isinstance(day, str) else day.strftime("%Y%m%d")
    used = set()
    for value in existing:
        try:
            p, d, seq = parse_id(value)
        except DomainError:
            continue
        if p == prefix and d == day_str:
            used.add(seq)
    seq = start
    while seq in used:
        seq += 1
    return format_id(prefix, day_str, seq)


def validate_transition(entity: str, current, target) -> None:
    """校验状态迁移；非法抛 DomainError。"""
    table = TRANSITIONS.get(entity)
    if table is None:
        raise DomainError(f"未知实体: {entity}")
    current = _as_enum(entity, current)
    target = _as_enum(entity, target)
    if target not in table.get(current, set()):
        raise DomainError(f"非法状态迁移: {entity} {current.value} -> {target.value}")


def _as_enum(entity: str, value):
    classes = {
        "hypothesis": HypothesisStatus,
        "experiment": ExperimentStatus,
        "strategy": StrategyStatus,
        "deployment": DeploymentStatus,
        "decision": DecisionStatus,
    }
    cls = classes.get(entity)
    if cls is None:
        raise DomainError(f"未知实体: {entity}")
    if isinstance(value, cls):
        return value
    try:
        return cls(value)
    except ValueError as e:
        raise DomainError(f"非法状态值: {entity}={value!r}") from e


# ── 领域对象 ────────────────────────────────────────────────────────────────

@dataclass
class MarketState:
    date: str
    metrics: dict[str, float] = field(default_factory=dict)
    regime: str = ""
    drivers: dict[str, str] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketState":
        return cls(
            date=str(data["date"]),
            metrics=dict(data.get("metrics") or {}),
            regime=str(data.get("regime") or ""),
            drivers=dict(data.get("drivers") or {}),
            quality=dict(data.get("quality") or {}),
        )


@dataclass
class Hypothesis:
    id: str
    version: int
    prior: str
    statement: str
    universe: str
    signal: dict[str, Any]
    horizon_days: int
    benchmark: str
    metric: str
    threshold: str
    regimes: list[str]
    costs: dict[str, float]
    kill_criteria: list[str]
    status: str
    owner: str = "human"
    created_at: str = ""
    locked_at: str = ""
    review_date: str = ""
    supersedes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})

    def lock(self) -> None:
        validate_transition("hypothesis", self.status, HypothesisStatus.LOCKED)
        self.status = HypothesisStatus.LOCKED.value


@dataclass
class Experiment:
    id: str
    hypothesis_id: str
    plan: dict[str, Any]
    data_snapshot_id: str
    code_commit: str
    config_hash: str
    random_seed: int
    status: str
    started_at: str = ""
    finished_at: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Experiment":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class ExperimentResult:
    id: str
    experiment_id: str
    metric: str
    value: float
    ci_low: float = 0.0
    ci_high: float = 0.0
    p_value: float = 0.0
    sample_size: int = 0
    splits: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""
    notes: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperimentResult":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class StrategyVersion:
    id: str
    version: int
    parent_id: str
    spec: dict[str, Any]
    status: str
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    approved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyVersion":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class Deployment:
    id: str
    strategy_id: str
    mode: str
    start_date: str
    end_date: str
    capital: float
    broker: str
    status: str
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Deployment":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class Attribution:
    id: str
    deployment_id: str
    period_start: str
    period_end: str
    total_return: float
    excess_return: float
    costs: float
    factor: dict[str, float] = field(default_factory=dict)
    sector: dict[str, float] = field(default_factory=dict)
    stock: dict[str, float] = field(default_factory=dict)
    regime: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Attribution":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class Decision:
    id: str
    decision_date: str
    type: str
    target: str
    change: str
    rationale: str
    evidence: dict[str, str]
    expected: str
    review_date: str
    status: str
    outcome: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Decision":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})
