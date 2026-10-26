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


# ── 事件研究：analysis_outcome 分层（R2-0 修 G1/G3/G4） ─────────────────────

def block_bootstrap_ci(values: list[float], groups: list[str],
                       n_boot: int = 1000, seed: int = 42,
                       confidence: float = 0.95) -> tuple[float | None, float | None]:
    """按**分组**（如股票）重抽样的 block bootstrap CI。

    动机：60/120 日前向窗口相互重叠、同一股票被多次分析，按行重抽样会
    高估显著性；按股票分块重抽样保留组内相关性。
    """
    if not values or not groups or len(values) != len(groups):
        return None, None
    import random
    import statistics
    by_group: dict[str, list[float]] = {}
    for value, group in zip(values, groups):
        by_group.setdefault(group, []).append(value)
    names = list(by_group)
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(names) for _ in names]
        pooled = [v for name in sample for v in by_group[name]]
        if pooled:
            means.append(sum(pooled) / len(pooled))
    if not means:
        return None, None
    means.sort()
    alpha = (1 - confidence) / 2
    lo = means[int(alpha * len(means))]
    hi = means[min(len(means) - 1, int((1 - alpha) * len(means)))]
    return round(lo, 6), round(hi, 6)


def _load_outcomes(start_date: str, end_date: str,
                   benchmark: str | None = None) -> list[dict]:
    sql = ("SELECT stock_code, analysis_date, signal, confidence, "
           "mos_at_entry, ret_20, ret_60, excess_20, excess_60 "
           "FROM analysis_outcome WHERE analysis_date BETWEEN ? AND ?")
    params: list = [start_date, end_date]
    if benchmark:
        sql += " AND benchmark_code = ?"
        params.append(benchmark)
    sql += " ORDER BY analysis_date"
    with db_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _join_regime(rows: list[dict]) -> None:
    """给每行补 regime（join market_state_daily）。"""
    with db_conn() as conn:
        for row in rows:
            hit = conn.execute(
                "SELECT regime FROM market_state_daily WHERE trade_date = ?",
                (row["analysis_date"],)).fetchone()
            row["regime"] = hit["regime"] if hit else None


def _join_sector_size(rows: list[dict]) -> None:
    """给每行补 sector / size（join stock_snapshot）。"""
    with db_conn() as conn:
        for row in rows:
            hit = conn.execute(
                "SELECT sector, market_cap FROM stock_snapshot WHERE code = ? "
                "ORDER BY snapshot_date DESC LIMIT 1", (row["stock_code"],)).fetchone()
            row["sector"] = hit["sector"] if hit else None
            cap = hit["market_cap"] if hit else None
            row["size"] = ("large" if (cap or 0) >= 1e10 else
                           "mid" if (cap or 0) >= 1e9 else "small")


class OutcomeEventStudyRunner(ExperimentRunner):
    """对 ``analysis_outcome`` 做分层事件研究（默认 MOS 分档）。

    计划参数（``plan.params``）：
    - ``tiers``：``[[label, lo, hi], ...]``（MOS 百分数，None 表示无穷）
    - ``horizons``：``[20, 60]``
    - ``n_boot``（默认 1000）、``splits``（默认 ``["regime","sector","size"]``）
    """

    name = "outcome_event_study"

    def run(self, plan: ExperimentPlan,
            snapshot: SnapshotManifest) -> ExperimentOutcome:
        started = now_cn().isoformat()
        try:
            metrics = self._compute(plan)
        except Exception as exc:  # noqa: BLE001 - 运行失败转实验状态
            return ExperimentOutcome(
                experiment_id=plan.experiment_id,
                status=ExperimentStatus.FAILED.value,
                started_at=started, finished_at=now_cn().isoformat(),
                error=f"{type(exc).__name__}: {exc}")
        results = [
            ExperimentResult(
                id=rid, experiment_id=plan.experiment_id,
                metric=metric, value=round(value, 6),
                sample_size=metric_stats["n"],
                ci_low=metric_stats.get("ci_low") or 0.0,
                ci_high=metric_stats.get("ci_high") or 0.0,
                splits=metric_stats.get("splits", {}),
                notes="分域明细" if metric_stats.get("splits") else "",
            )
            for rid, (metric, value, metric_stats) in zip(
                _result_ids(plan, len(metrics)), metrics)
        ]
        outcome = ExperimentOutcome(
            experiment_id=plan.experiment_id,
            status=ExperimentStatus.DONE.value,
            started_at=started, finished_at=now_cn().isoformat(),
            results=results,
            notes=self._notes(plan, metrics))
        outcome.verdict = derive_verdict(plan, outcome)
        return outcome

    @staticmethod
    def _notes(plan: ExperimentPlan, metrics: list) -> str:
        p = plan.params
        with db_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM instrument").fetchone()["n"]
            row = conn.execute(
                "SELECT COUNT(DISTINCT stock_code) AS n FROM analysis_outcome "
                "WHERE analysis_date BETWEEN ? AND ?",
                (p.get("start_date"), p.get("end_date"))).fetchone()
        stocks = row["n"] if row else 0
        holdout = (f"；holdout 末尾 {p['holdout_days']} 自然日"
                   if p.get("holdout_days") else "")
        return (f"事件研究；样本 {stocks} 只股票 / 全市场 {total} 只"
                f"（**可能存在幸存者偏差**：退市股无历史分析）；"
                f"CI 为按股票分块的 block bootstrap；共 {len(metrics)} 个指标"
                f"{holdout}；结论需按多重比较（R2-6 对照假设）解读")

    def _compute(self, plan: ExperimentPlan) -> list[tuple[str, float, dict]]:
        p = plan.params
        start, end = p["start_date"], p["end_date"]
        tiers = p.get("tiers") or [
            ["<=0", None, 0.0], ["0~20", 0.0, 20.0],
            ["20~40", 20.0, 40.0], [">=40", 40.0, None]]
        horizons = p.get("horizons") or [20, 60]
        n_boot = int(p.get("n_boot", 1000))
        splits = p.get("splits") or ["regime", "sector", "size"]
        rows = _load_outcomes(start, end, plan.benchmark or None)
        if "regime" in splits:
            _join_regime(rows)
        if "sector" in splits or "size" in splits:
            _join_sector_size(rows)

        # holdout：留出区间末尾 N 个自然日做样本外（预注册用）
        holdout_days = int(p.get("holdout_days", 0))
        est_rows = rows
        hold_rows: list[dict] = []
        if holdout_days > 0:
            from datetime import date as _date, timedelta as _td
            cutoff = (_date.fromisoformat(end) - _td(days=holdout_days)).isoformat()
            est_rows = [r for r in rows if r["analysis_date"] <= cutoff]
            hold_rows = [r for r in rows if r["analysis_date"] > cutoff]

        out = self._tier_metrics(plan, est_rows, tiers, horizons, n_boot, splits)
        if hold_rows:
            out += [("holdout_" + m, v, s) for m, v, s in
                    self._tier_metrics(plan, hold_rows, tiers, horizons, n_boot, [])]
        return out

    def _tier_metrics(self, plan: ExperimentPlan, rows: list[dict],
                      tiers: list, horizons: list, n_boot: int,
                      splits: list) -> list[tuple[str, float, dict]]:
        out: list[tuple[str, float, dict]] = []
        for horizon in horizons:
            suffix = f"excess_{horizon}"
            usable = [r for r in rows if r.get(suffix) is not None]
            for label, lo, hi in tiers:
                sel = [r for r in usable if _in_tier(r.get("mos_at_entry"), lo, hi)]
                if not sel:
                    continue
                values = [r[suffix] for r in sel]
                groups = [r["stock_code"] for r in sel]
                mean = sum(values) / len(values)
                ci_low, ci_high = block_bootstrap_ci(values, groups, n_boot,
                                                     plan.random_seed)
                split_stats = {}
                for key in splits:
                    bucket: dict[str, list[float]] = {}
                    for r in sel:
                        bucket.setdefault(str(r.get(key)), []).append(r[suffix])
                    split_stats[key] = {
                        k: round(sum(v) / len(v), 6) for k, v in bucket.items()}
                out.append((
                    f"excess_{horizon}_{label}", round(mean, 6),
                    {"n": len(sel), "ci_low": ci_low, "ci_high": ci_high,
                     "hit_rate": round(sum(1 for v in values if v > 0) / len(values), 4),
                     "splits": split_stats}))
        return out


def _in_tier(value: float | None, lo: float | None, hi: float | None) -> bool:
    if value is None:
        return False
    if lo is not None and value < lo:
        return False
    if hi is not None and value >= hi:
        return False
    return True


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
    # 已知数据集且无质量记录：运行时自动评估并补写（未注册评估器 -> fail-closed）。
    # 已有记录（如预注册的坏数据）不被覆盖。
    if dao.get_quality(plan.dataset, qdate) is None:
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
