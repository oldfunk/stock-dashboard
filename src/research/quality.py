"""数据质量门（2026-10-26，R0-5）。

规则（默认，可被 config/research.yaml 覆盖）：
- coverage >= 0.95
- missing_rate <= 0.05
- outliers == 0
- pit_violations == 0
- 陈旧度：trade_date 早于今天 max_age_days 天 -> warn

状态：
- pass：全部通过
- warn：仅缺失/覆盖轻微超标（仍可用于探索，但不晋升）
- invalid：PIT 违规、异常值、或缺失严重；实验必须标记 invalid
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from src.research.dao import ResearchDAO


class QualityError(ValueError):
    """数据质量不达标。"""


DEFAULTS = {
    "coverage_min": 0.95,
    "missing_rate_max": 0.05,
    "outliers_max": 0,
    "pit_violations_max": 0,
    "max_age_days": 3,
}


@dataclass
class QualityResult:
    dataset: str
    trade_date: str
    status: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("pass", "warn")


def evaluate(dataset: str, trade_date: str,
             thresholds: dict[str, float] | None = None,
             dao: ResearchDAO | None = None) -> QualityResult:
    """读取 data_quality_daily 并判定；无记录 = invalid（fail-closed）。

    按 (dataset, trade_date) 精确查询，不做"取最近 N 条再过滤"——后者会让
    早于窗口的日期被误判为缺记录（静默错误）。
    """
    dao = dao or ResearchDAO()
    row = dao.get_quality(dataset, trade_date)
    if row is None:
        return QualityResult(dataset, trade_date, "invalid",
                             ["缺少质量记录（fail-closed）"])
    t = {**DEFAULTS, **(thresholds or {})}
    reasons: list[str] = []
    invalid = False
    warn = False

    coverage = row.get("coverage")
    if coverage is None or coverage < t["coverage_min"]:
        reasons.append(f"覆盖率 {coverage} < {t['coverage_min']}")
        invalid = True

    missing = row.get("missing_rate")
    if missing is None or missing > t["missing_rate_max"]:
        reasons.append(f"缺失率 {missing} > {t['missing_rate_max']}")
        warn = True

    outliers = row.get("outliers")
    if outliers is None or outliers > t["outliers_max"]:
        reasons.append(f"异常值 {outliers} > {t['outliers_max']}")
        invalid = True

    pit = row.get("pit_violations")
    if pit is None or pit > t["pit_violations_max"]:
        reasons.append(f"PIT 违规 {pit} > {t['pit_violations_max']}")
        invalid = True

    try:
        d = datetime.strptime(trade_date, "%Y-%m-%d").date()
        age = (date.today() - d).days
        if age > t["max_age_days"]:
            reasons.append(f"数据陈旧 {age} 天 > {t['max_age_days']}")
            warn = True
    except ValueError:
        reasons.append("trade_date 非法")
        invalid = True

    status = "invalid" if invalid else ("warn" if warn else "pass")
    return QualityResult(dataset, trade_date, status, reasons, dict(row))


def assert_quality(dataset: str, trade_date: str,
                   thresholds: dict[str, float] | None = None,
                   dao: ResearchDAO | None = None) -> QualityResult:
    """不达标抛 QualityError；调用方（实验）据此标记 invalid。"""
    result = evaluate(dataset, trade_date, thresholds, dao)
    if not result.ok:
        raise QualityError(
            f"{dataset} {trade_date} 质量不达标: {'; '.join(result.reasons)}")
    return result


def record_quality(dataset: str, trade_date: str, coverage: float | None,
                   missing_rate: float | None, outliers: int | None,
                   pit_violations: int | None, details: dict | None = None,
                   dao: ResearchDAO | None = None) -> QualityResult:
    """写入质量记录并返回判定。"""
    dao = dao or ResearchDAO()
    dao.insert_quality({
        "dataset": dataset, "trade_date": trade_date, "coverage": coverage,
        "missing_rate": missing_rate, "outliers": outliers,
        "pit_violations": pit_violations, "status": "recorded",
        "details": details or {},
    })
    return evaluate(dataset, trade_date, dao=dao)
