"""Research OS 实验接口（2026-10-26，R0-6）。

职责：
- ``ExperimentPlan``：实验计划（绑定假设、数据快照、参数、费用）。
- ``ExperimentRunner``：抽象接口，**不绑死单一回测器**。
- ``TopKBacktestRunner``：最小可复现的 top-k 因子回测适配器（示例实现）。
- ``run_experiment``：校验快照 -> 数据质量门 -> 运行 -> 落库。

原则：
- 快照缺失/漂移 -> 拒绝（``SnapshotError``）。
- 质量门 ``invalid`` -> 实验直接 ``invalid``，不产出结论。
- 结果全部落 ``research_result``，含分域（rebalance）明细，可复现。
- 随机种子只影响需要随机性的实现；本适配器为确定性排序（并列按 ID）。
"""

from __future__ import annotations

import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any

from src.models.database import db_conn, now_cn
from src.research.dao import ResearchDAO
from src.research.domain import (
    Experiment, ExperimentResult, ExperimentStatus, next_id,
)
from src.research.quality import assess_dataset, evaluate, record_quality
from src.research.snapshot import SnapshotManifest, verify_snapshot


class ExperimentError(ValueError):
    """实验框架错误（缺快照、缺假设、参数非法等）。"""


# ── 计划与结果 ──────────────────────────────────────────────────────────────

@dataclass
class ExperimentPlan:
    experiment_id: str
    hypothesis_id: str
    data_snapshot_id: str
    dataset: str
    universe: str
    benchmark: str
    metric: str
    threshold: str
    horizon_days: int
    regimes: list[str] = field(default_factory=list)
    costs: dict[str, float] = field(default_factory=dict)
    random_seed: int = 42
    code_commit: str | None = None
    config_hash: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentOutcome:
    experiment_id: str
    status: str
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    results: list[ExperimentResult] = field(default_factory=list)
    verdict: str = ""
    notes: str = ""

    def metric_values(self) -> dict[str, float]:
        return {r.metric: r.value for r in self.results}

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "verdict": self.verdict,
            "notes": self.notes,
            "results": [r.to_dict() for r in self.results],
        }


# ── 阈值解析与结论 ──────────────────────────────────────────────────────────

_OPS = ("<=", ">=", "==", "<", ">")


def _compare(value: float, threshold: str) -> bool | None:
    """解析 ``>0`` / ``>=0.02`` / ``<0``；无法解析返回 None。"""
    text = (threshold or "").strip().replace(" ", "")
    for op in _OPS:
        if text.startswith(op):
            num = text[len(op):]
            try:
                target = float(num)
            except ValueError:
                return None
            return {
                "<": value < target,
                "<=": value <= target,
                ">": value > target,
                ">=": value >= target,
                "==": value == target,
            }[op]
    return None


def derive_verdict(plan: ExperimentPlan, outcome: ExperimentOutcome) -> str:
    """按计划的 metric/threshold 判定结论；数据不足或阈值不可解析 -> inconclusive。"""
    values = outcome.metric_values()
    if plan.metric not in values:
        return "inconclusive"
    if not outcome.results:
        return "inconclusive"
    ok = _compare(values[plan.metric], plan.threshold)
    if ok is None:
        return "inconclusive"
    return "supported" if ok else "rejected"


# ── 运行器接口 ──────────────────────────────────────────────────────────────

class ExperimentRunner(ABC):
    """实验运行器接口：任何回测/事件研究实现都可接入。"""

    name: str = "abstract"

    @abstractmethod
    def run(self, plan: ExperimentPlan,
            snapshot: SnapshotManifest) -> ExperimentOutcome:
        """执行实验并返回结果（含 verdict）。"""


# ── top-k 因子回测适配器 ────────────────────────────────────────────────────

def _result_ids(plan: ExperimentPlan, count: int) -> list[str]:
    """生成可解析的结果 ID（R-YYYYMMDD-NNN），与实体 ID 同规则。"""
    parts = plan.experiment_id.split("-")
    day = (parts[1] if len(parts) >= 2 and len(parts[1]) == 8
           else now_cn().strftime("%Y%m%d"))
    with db_conn() as conn:
        existing = [r["id"] for r in conn.execute("SELECT id FROM research_result")]
    ids: list[str] = []
    for _ in range(count):
        rid = next_id("R", day, existing + ids)
        ids.append(rid)
    return ids


def _load_close_series(start: str, end: str) -> tuple[dict[str, dict[str, float]], list[str]]:
    """读取 [start, end] 的前复权收盘（close_adj）。

    ``instrument_id`` 可能尚未迁移（生产回填中），用 ``COALESCE`` 回退到
    ``CN:code``，保证迁移前后都能跑。
    """
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(instrument_id, 'CN:' || code) AS iid, "
            "trade_date, close_adj FROM kline_daily "
            "WHERE close_adj IS NOT NULL AND trade_date BETWEEN ? AND ? "
            "ORDER BY iid, trade_date",
            (start, end)).fetchall()
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM kline_daily "
            "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (start, end)).fetchall()]
    series: dict[str, dict[str, float]] = {}
    for r in rows:
        series.setdefault(r["iid"], {})[r["trade_date"]] = r["close_adj"]
    return series, dates


def _load_features(trade_date: str, feature_id: str,
                   feature_version: int) -> dict[str, float]:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT instrument_id, value FROM stock_feature_daily "
            "WHERE trade_date = ? AND feature_id = ? AND feature_version = ? "
            "AND value IS NOT NULL",
            (trade_date, feature_id, feature_version)).fetchall()
    return {r["instrument_id"]: r["value"] for r in rows}


class TopKBacktestRunner(ExperimentRunner):
    """最小 top-k 因子回测：按特征排序取前 k，持有 horizon_days 个交易日。

    计划参数（``plan.params``）：
    - ``feature_id``（必填）、``feature_version``（默认 1）
    - ``start_date`` / ``end_date``（必填，闭区间）
    - ``topk``（默认 10）、``rebalance_days``（默认 21，按交易日步进）
    费用（``plan.costs``）：``commission`` / ``slippage``（双边各一次）。
    基准：全体有有效价格品种的等权前向收益（``benchmark="equal_weight"``）。
    """

    name = "topk_backtest"

    def run(self, plan: ExperimentPlan,
            snapshot: SnapshotManifest) -> ExperimentOutcome:
        started = now_cn().isoformat()
        try:
            rebalances, summary = self._compute(plan)
        except Exception as exc:  # noqa: BLE001 - 运行失败转实验状态
            return ExperimentOutcome(
                experiment_id=plan.experiment_id,
                status=ExperimentStatus.FAILED.value,
                started_at=started, finished_at=now_cn().isoformat(),
                error=f"{type(exc).__name__}: {exc}")

        result_ids = _result_ids(plan, len(summary["metrics"]))
        results = [
            ExperimentResult(
                id=rid,
                experiment_id=plan.experiment_id,
                metric=metric, value=round(value, 6),
                sample_size=summary["n_rebalances"],
                splits={"rebalances": rebalances} if metric == "mean_excess" else {},
                notes=("分域明细" if metric == "mean_excess" else ""),
            )
            for rid, (metric, value) in zip(result_ids, summary["metrics"].items())
        ]
        outcome = ExperimentOutcome(
            experiment_id=plan.experiment_id,
            status=(ExperimentStatus.DONE.value if rebalances
                    else ExperimentStatus.INVALID.value),
            started_at=started, finished_at=now_cn().isoformat(),
            results=results,
            notes=(f"快照 {snapshot.snapshot_id}；runner={self.name}；"
                   f"再平衡 {summary['n_rebalances']} 次"))
        outcome.verdict = derive_verdict(plan, outcome)
        return outcome

    def _compute(self, plan: ExperimentPlan) -> tuple[list[dict], dict]:
        p = plan.params
        for key in ("feature_id", "start_date", "end_date"):
            if not p.get(key):
                raise ExperimentError(f"缺少参数: {key}")
        feature_id = p["feature_id"]
        feature_version = int(p.get("feature_version", 1))
        topk = int(p.get("topk", 10))
        horizon = int(plan.horizon_days)
        step = int(p.get("rebalance_days", 21))
        if topk < 1 or horizon < 1 or step < 1:
            raise ExperimentError("topk/horizon_days/rebalance_days 必须 >= 1")
        commission = float(plan.costs.get("commission", 0.0))
        slippage = float(plan.costs.get("slippage", 0.0))

        series, dates = _load_close_series(p["start_date"], p["end_date"])
        rebalances: list[dict] = []
        for i in range(0, len(dates), step):
            trade_date = dates[i]
            feats = _load_features(trade_date, feature_id, feature_version)
            if not feats:
                continue
            ranked = sorted(feats.items(), key=lambda kv: (-kv[1], kv[0]))
            selected = [iid for iid, _ in ranked[:topk]]
            entry_idx, exit_idx = i + 1, i + 1 + horizon
            if exit_idx >= len(dates):
                continue
            entry_date, exit_date = dates[entry_idx], dates[exit_idx]
            picks = []
            for iid in selected:
                s = series.get(iid) or {}
                e, x = s.get(entry_date), s.get(exit_date)
                if e is None or x is None or e <= 0:
                    continue
                picks.append(x / e - 1.0 - 2 * (commission + slippage))
            bench = []
            for s in series.values():
                e, x = s.get(entry_date), s.get(exit_date)
                if e is None or x is None or e <= 0:
                    continue
                bench.append(x / e - 1.0)
            if not picks or not bench:
                continue
            port = sum(picks) / len(picks)
            bm = sum(bench) / len(bench)
            rebalances.append({"date": trade_date, "portfolio": round(port, 6),
                               "benchmark": round(bm, 6),
                               "excess": round(port - bm, 6), "n": len(picks)})

        if not rebalances:
            return [], {"n_rebalances": 0, "metrics": {}}
        n = len(rebalances)
        rets = [r["portfolio"] for r in rebalances]
        excs = [r["excess"] for r in rebalances]
        total = 1.0
        for value in rets:
            total *= (1 + value)
        metrics = {
            "mean_return": sum(rets) / n,
            "mean_excess": sum(excs) / n,
            "mean_benchmark": sum(r["benchmark"] for r in rebalances) / n,
            "hit_rate": sum(1 for e in excs if e > 0) / n,
            "return_std": (statistics.pstdev(rets) if n > 1 else 0.0),
            "n_rebalances": float(n),
            "total_return": total - 1.0,
        }
        return rebalances, {"n_rebalances": n, "metrics": metrics}


# ── 编排：快照 -> 质量门 -> 运行 -> 落库 ────────────────────────────────────

def _quality_date(plan: ExperimentPlan, manifest: SnapshotManifest) -> str:
    """取质量门判定日期：计划 end_date > 快照最大日期 > 计划 start_date。"""
    stat = manifest.tables.get(plan.dataset)
    return (plan.params.get("end_date")
            or (stat.max_date if stat else None)
            or plan.params.get("start_date") or "")


def run_experiment(runner: ExperimentRunner, plan: ExperimentPlan,
                   dao: ResearchDAO | None = None,
                   thresholds: dict[str, float] | None = None) -> ExperimentOutcome:
    """执行实验并落库。

    步骤：校验快照 -> 校验假设存在 -> 质量门 -> 运行 -> 写 experiment/result。
    """
    dao = dao or ResearchDAO()
    if not plan.data_snapshot_id:
        raise ExperimentError("缺少 data_snapshot_id（快照缺失实验拒绝）")
    manifest = verify_snapshot(plan.data_snapshot_id)  # 缺失/漂移抛 SnapshotError

    hypothesis = dao.get_hypothesis(plan.hypothesis_id)
    if hypothesis is None:
        raise ExperimentError(f"假设不存在: {plan.hypothesis_id}")

    qdate = _quality_date(plan, manifest)
    # 已知数据集：运行时自动评估并写质量记录（未注册评估器 -> fail-closed）
    stats = assess_dataset(plan.dataset, qdate, dao)
    if stats is not None:
        record_quality(plan.dataset, qdate, **stats, dao=dao)
    quality = evaluate(plan.dataset, qdate, thresholds)
    if quality.status == "invalid":
        outcome = ExperimentOutcome(
            experiment_id=plan.experiment_id,
            status=ExperimentStatus.INVALID.value,
            started_at=now_cn().isoformat(),
            finished_at=now_cn().isoformat(),
            verdict="invalid",
            notes=f"数据质量门未通过: {'; '.join(quality.reasons)}")
        _persist(dao, plan, outcome)
        return outcome

    outcome = runner.run(plan, manifest)
    _persist(dao, plan, outcome)
    return outcome


def _persist(dao: ResearchDAO, plan: ExperimentPlan,
             outcome: ExperimentOutcome) -> None:
    dao.insert_experiment(Experiment(
        id=plan.experiment_id,
        hypothesis_id=plan.hypothesis_id,
        plan=plan.to_dict(),
        data_snapshot_id=plan.data_snapshot_id,
        code_commit=plan.code_commit,
        config_hash=plan.config_hash,
        random_seed=plan.random_seed,
        status=outcome.status,
        started_at=outcome.started_at,
        finished_at=outcome.finished_at,
        error=outcome.error,
    ))
    for result in outcome.results:
        dao.insert_result(result)
