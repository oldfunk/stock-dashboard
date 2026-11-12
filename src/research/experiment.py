"""Research OS 实验接口（2026-10-26，R0-6；2026-10-27 R2-2 增计划 CLI）。

职责：
- ``ExperimentPlan``：实验计划（绑定假设、数据快照、参数、费用）。
- ``ExperimentRunner``：抽象接口，**不绑死单一回测器**。
- ``TopKBacktestRunner``：最小可复现的 top-k 因子回测适配器（示例实现）。
- ``run_experiment``：校验快照 -> 数据质量门 -> 运行 -> 落库。
- ``main``：计划 CLI（R2-2）—— ``create`` 预注册（planned）/ ``view`` / ``list``。

原则：
- 快照缺失/漂移 -> 拒绝（``SnapshotError``）。
- 质量门 ``invalid`` -> 实验直接 ``invalid``，不产出结论。
- 结果全部落 ``research_result``，含分域（rebalance）明细，可复现。
- 随机种子只影响需要随机性的实现；本适配器为确定性排序（并列按 ID）。
- 预注册（R2-2）：``create`` 落 planned 状态即**绑定**假设/快照/指标/分域/
  费用——同 ID 拒绝覆盖（预注册不可事后篡改），假设或快照在注册时即校验
  （fail-fast，不留到运行时才爆）。

用法（R2-2 计划 CLI）::

    python -m src.research.experiment create \\
        --hypothesis H-20261027-001 --snapshot DS-20261027 \\
        --dataset analysis_outcome --metric excess --threshold ">0" \\
        --horizon-days 60 --regimes all --costs '{"slippage": 0.001}' \\
        --params '{"start_date": "2026-07-01", "end_date": "2026-09-30"}'
    python -m src.research.experiment view E-20261027-001
    python -m src.research.experiment list --status planned
"""

from __future__ import annotations

import argparse
import json
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any

from src.models.database import db_conn, now_cn
from src.research.dao import ResearchDAO
from src.research.domain import (
    CI_RULES, Experiment, ExperimentResult, ExperimentStatus, next_id,
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
    ci_rule: str = "none"          # none | exclude_zero；默认不看 CI，预注册可要求看
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


def _ci_covers_zero(ci_low: float | None, ci_high: float | None) -> bool | None:
    """CI 是否覆盖 0；任一端缺失返回 None（未计算，不能声称"不覆盖"）。"""
    if ci_low is None or ci_high is None:
        return None
    return ci_low <= 0.0 <= ci_high


def derive_verdict(plan: ExperimentPlan, outcome: ExperimentOutcome) -> str:
    """按计划的 metric/threshold 判定结论；数据不足或阈值不可解析 -> inconclusive。

    ``ci_rule='exclude_zero'``（Q1 裁定，批18-b）：点估计满足阈值**且** CI 不覆盖 0
    才判 supported；CI 覆盖 0 时判 inconclusive——这种结果排除不了"没有效果"，
    落进 supported 正是 2026-11-12 合成库演示的缺陷（零关系库 CI [-0.38,1.16]
    覆盖 0，旧逻辑仍给 supported）。``none``（默认）保持原语义：只看轴估计，
    不让新老实验口径分叉。
    """
    values = outcome.metric_values()
    if plan.metric not in values:
        return "inconclusive"
    if not outcome.results:
        return "inconclusive"
    ok = _compare(values[plan.metric], plan.threshold)
    if ok is None:
        return "inconclusive"
    if not ok:
        return "rejected"
    rule = (plan.ci_rule or "none").strip() or "none"
    if rule == "exclude_zero":
        r = next((x for x in outcome.results if x.metric == plan.metric), None)
        covers = _ci_covers_zero(getattr(r, "ci_low", None),
                                 getattr(r, "ci_high", None))
        if covers is None:
            return "inconclusive"       # 没 CI 就不能声称"不覆盖 0"
        if covers:
            return "inconclusive"
    return "supported"


def bonferroni_confidence(plan: "ExperimentPlan", base: float = 0.95) -> float:
    """Q2：同实验指标族内的 Bonferroni 校正置信度。

    ``params['bonferroni_m'] = m``（默认 1 = 不校正）。m 个指标同时看时，
    单个 CI 的置信度要收紧到 ``1 - (1-base)/m``，族内的整体置信度才回到 base。
    跨实验不合并（E-H001/E-H001b 是复跑，不是独立证据）。
    """
    try:
        m = int(plan.params.get("bonferroni_m") or 1)
    except (TypeError, ValueError):
        m = 1
    if m <= 1:
        return base
    return 1.0 - (1.0 - base) / m


def mark_result_verdicts(plan: ExperimentPlan, outcome: ExperimentOutcome) -> None:
    """Q3：result 级 verdict 只填**预注册判据**那一条。

    其余指标是辅助证据。给它们也填 verdict，事后就分不清"预注册判据说支持"
    与"顺手看的指标恰好为正"——多重比较的失真正来自这种混淆。落库时给非判据
    指标留空并注明。
    """
    for r in outcome.results:
        if r.metric == plan.metric:
            r.verdict = outcome.verdict
        elif not r.notes:
            r.notes = "未预注册为判据（辅助指标）"


def inconclusive_reason(plan: ExperimentPlan, outcome: "ExperimentOutcome") -> str:
    """R2-5：判不了的时候要写清**缺的是哪一种证据**。

    `derive_verdict` 有三种情况给 inconclusive，落在库里如果长一个样，事后就只能猜：
      1. 一条结果都没有（窗口内没有可算指标）；
      2. 计划判据不在实报指标集合里（改名/分档变了/预注册写错）；
      3. 阈值不可解析或与结果不可比。
    """
    if not outcome.results:
        return "无结果行：窗口内没有可算指标（样本或字段缺失）"
    values = outcome.metric_values()
    if plan.metric not in values:
        return (f"计划判据 {plan.metric} 不在实报的 {len(values)} 个指标里"
                f"（{', '.join(sorted(values)[:6])}…）")
    if _compare(values[plan.metric], plan.threshold) is None:
        return f"阈值 {plan.threshold!r} 不可解析，或与结果量纲不可比"
    return ""


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


def _block_bootstrap_diff_ci(ga: dict, gb: dict, n_boot: int, seed: int,
                             confidence: float = 0.95) -> tuple[float | None, float | None]:
    """两独立分层（股票分块）均值差的 block bootstrap CI——净增量判据用。

    与 ``block_bootstrap_ci`` 同规：每侧按股票重抽样（保留组内相关），
    差分分布取分位数。两侧股票零重叠时尤其要注意不能按行混合重抽。
    """
    if not ga or not gb:
        return None, None
    import random
    rng = random.Random(seed)
    na, nb = list(ga), list(gb)
    diffs = []
    for _ in range(n_boot):
        sa = [rng.choice(na) for _ in na]
        sb = [rng.choice(nb) for _ in nb]
        va = [v for name in sa for v in ga[name]]
        vb = [v for name in sb for v in gb[name]]
        if va and vb:
            diffs.append(sum(va) / len(va) - sum(vb) / len(vb))
    if not diffs:
        return None, None
    diffs.sort()
    alpha = (1 - confidence) / 2
    lo = diffs[int(alpha * len(diffs))]
    hi = diffs[min(len(diffs) - 1, int((1 - alpha) * len(diffs)))]
    return round(lo, 6), round(hi, 6)


def _sole_benchmark(start_date: str, end_date: str) -> str | None:
    """计划没写 benchmark 时的兜底：窗口里只有一种基准就用它，多于一种必须显式指定。

    审计 **U5**（2026-11-03 复核成立）：`analysis_outcome` 主键含 `benchmark_code`，
    产线现状是**同一批分析对两个基准各有一行**（只读实测 sh000300 / sh000922 各
    390 行、日期区间完全重叠）。旧写法 `plan.benchmark or None` 会让空 benchmark
    的计划**不过滤**，后果有两条：样本量按基准数翻倍，以及把两种超额口径
    （相对沪深300 与相对中证500 的 excess）混进同一个均值与 bootstrap CI——
    那不是任何一个可解释的数字。
    """
    with db_conn() as conn:
        codes = [r["benchmark_code"] for r in conn.execute(
            "SELECT DISTINCT benchmark_code FROM analysis_outcome"
            " WHERE SUBSTR(analysis_date, 1, 10) BETWEEN ? AND ?",
            (start_date, end_date)).fetchall()]
    if not codes:
        return None
    if len(codes) > 1:
        raise ExperimentError(
            f"计划未指定 benchmark，但窗口内有 {len(codes)} 个基准"
            f"（{', '.join(sorted(codes)[:6])}）：超额收益口径不能混算，"
            "请在实验计划里写明 benchmark")
    return codes[0]


def _load_outcomes(start_date: str, end_date: str,
                   benchmark: str | None = None) -> list[dict]:
    # 窗口按**日期**比，不按整串比（P2-H2-1 的同一类，这里是第三处）：
    # `analysis_date BETWEEN '2026-07-08' AND '2026-07-08'` 会把
    # "2026-07-08 09:30" 静默排除在单日窗口外——样本无声变少，且 notes 里
    # 的分母与实到样本还会不一致。代价是放弃 idx_analysis_outcome_date
    # （产线 780 行；一次扫描远便宜于 block bootstrap）。
    sql = ("SELECT stock_code, analysis_date, signal, confidence, "
           "mos_at_entry, ret_20, ret_60, excess_20, excess_60 "
           "FROM analysis_outcome"
           " WHERE SUBSTR(analysis_date, 1, 10) BETWEEN ? AND ?")
    params: list = [start_date, end_date]
    if benchmark:
        sql += " AND benchmark_code = ?"
        params.append(benchmark)
    sql += " ORDER BY analysis_date"
    with db_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _join_regime(rows: list[dict]) -> None:
    """给每行补 regime（join market_state_daily）。

    与 `_join_sector_size` 同样按**日期**对齐：`analysis_date` 带时间部分时
    `trade_date = '2026-07-08 09:30'` 会整列 join 不上，regime 全线落进 unknown
    （P2-H2-1 的同一类错，只是这里表现为"没数据"而不是"分错边"）。
    """
    with db_conn() as conn:
        for row in rows:
            hit = conn.execute(
                "SELECT regime FROM market_state_daily WHERE trade_date = ?",
                (str(row["analysis_date"])[:10],)).fetchone()
            row["regime"] = hit["regime"] if hit else None


def _size_bucket(cap) -> str | None:
    """市值（元）→ 大小盘分档。

    P1-H2-1 的另一半：**没有市值就是 None**。旧写法 `(cap or 0) >= 1e10 ...`
    一路落到 else，把"取不到市值"归成 `small`——退市股/未覆盖股会被算进小盘组，
    而小盘组恰恰是这类实验里最容易出"高收益"的桶。
    """
    if not cap or cap <= 0:
        return None
    if cap >= 1e10:
        return "large"
    return "mid" if cap >= 1e9 else "small"


def _join_sector_size(rows: list[dict]) -> None:
    """给每行补 sector / size —— **按该行 analysis_date 取 PIT 快照**。

    审计 P1-H2-1（Hermes H2，2026-11-04）：旧写法
    `ORDER BY snapshot_date DESC LIMIT 1` 不截日期，于是 7 月的分析用 11 月的市值
    分档（前视），且退市股在"最新快照"里往往根本没有行（幸存者偏差）。
    现在与 `_block_snapshot` / 事件研究的 PIT 口径一致：`snapshot_date <= 分析日`，
    取不到就 None（分域里进 `unknown` 桶，见 `_bucket_label`）。
    """
    with db_conn() as conn:
        for row in rows:
            hit = conn.execute(
                "SELECT sector, market_cap FROM stock_snapshot WHERE code = ? "
                "AND snapshot_date <= ? ORDER BY snapshot_date DESC LIMIT 1",
                (row["stock_code"], str(row["analysis_date"])[:10])).fetchone()
            row["sector"] = hit["sector"] if hit else None
            row["size"] = _size_bucket(hit["market_cap"] if hit else None)


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
            metrics, context = self._compute(plan)
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
                ci_low=metric_stats.get("ci_low"),
                ci_high=metric_stats.get("ci_high"),
                splits=metric_stats.get("splits", {}),
                notes="分域明细（每桶 {mean, n}）" if metric_stats.get("splits") else "",
            )
            for rid, (metric, value, metric_stats) in zip(
                _result_ids(plan, len(metrics)), metrics)
        ]
        outcome = ExperimentOutcome(
            experiment_id=plan.experiment_id,
            status=ExperimentStatus.DONE.value,
            started_at=started, finished_at=now_cn().isoformat(),
            results=results,
            notes=self._notes(plan, metrics, context))
        outcome.verdict = derive_verdict(plan, outcome)
        mark_result_verdicts(plan, outcome)
        return outcome

    @staticmethod
    def _notes(plan: ExperimentPlan, metrics: list, context: dict | None = None) -> str:
        p = plan.params
        ctx = context or {}
        with db_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM instrument").fetchone()["n"]
            row = conn.execute(
                "SELECT COUNT(DISTINCT stock_code) AS n FROM analysis_outcome "
                "WHERE SUBSTR(analysis_date, 1, 10) BETWEEN ? AND ?",
                (p.get("start_date"), p.get("end_date"))).fetchone()
        stocks = row["n"] if row else 0
        holdout = (f"；holdout 末尾 {p['holdout_days']} 自然日"
                   if p.get("holdout_days") else "")
        # P2-H2-2：两个数分开命名。旧文案"样本 X 只 / 全市场 Y 只"读起来像
        # "覆盖了 X/Y 的市场"，其实 X 是**分析期出现过的股票数**、Y 是**当前**
        # instrument 行数——退市股根本不在 Y 里，幸存者偏差被这个分母表述弱化了。
        cov = ""
        if ctx.get("rows"):
            parts = []
            for key, label in (("regime_known", "regime"), ("size_known", "size"),
                               ("sector_known", "sector")):
                if key in ctx:      # 只报本次真正 join 过的维度（splits 里没要就不报）
                    parts.append(f"{label} {ctx[key]}/{ctx['rows']}")
            cov = "分域覆盖率：" + "、".join(parts) if parts else ""
        unparsed = (f"；{ctx['unparsed_dates']} 行日期不可解析，留在估计集"
                    if ctx.get("unparsed_dates") else "")
        missing = (f"；缺分域标签的行进 unknown 桶，每桶样本数见 splits[维度][桶].n"
                   if ctx.get("rows") else "")
        # P1-H2-1 的实测后果（2026-11-05 生产只读）：stock_snapshot 是
        # `code` PRIMARY KEY + 每日 INSERT OR REPLACE，全库 5527 行=5527 个 code，
        # 表里**只有最新一行**，没有历史快照可 join。于是 PIT 口径下 780 行分析只
        # 命中 2 行——sector/size 分域在补出快照历史之前基本读不出东西。
        # 旧实现不是"覆盖率高"，是把 30/31 只股票都用**分析日之后**的市值分了档。
        known = [ctx[k] for k in ("size_known", "sector_known") if k in ctx]
        pit_note = ("；sector/size 取 stock_snapshot 的 PIT 快照，该表一码一行"
                    "（code 是 PRIMARY KEY，每日整行覆盖），历史分析日没有当日快照"
                    "时进 unknown——分域可用度受快照历史限制，不是样本本身的问题"
                    if known and ctx.get("rows") and min(known) < ctx["rows"]
                    else "")
        return (f"事件研究；分析期出现 {stocks} 只股票；instrument 表当前 {total} 只"
                f"（**两者不是同一集合**：退市股既不在 instrument 也无历史分析，"
                f"幸存者偏差由此来，不能用 {stocks}/{total} 当覆盖率）；"
                f"{cov}{unparsed}{missing}{pit_note}；"
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
        rows = _load_outcomes(start, end,
                              plan.benchmark or _sole_benchmark(start, end))
        if "regime" in splits:
            _join_regime(rows)
        if "sector" in splits or "size" in splits:
            _join_sector_size(rows)

        # holdout：留出区间末尾 N 个自然日做样本外（预注册用）
        holdout_days = int(p.get("holdout_days", 0))
        est_rows = rows
        hold_rows: list[dict] = []
        unparsed_dates = 0
        if holdout_days > 0:
            from datetime import date as _date, timedelta as _td
            cutoff = _date.fromisoformat(end) - _td(days=holdout_days)
            # P2-H2-1：按 date 比较，不按字符串。旧写法 `r["analysis_date"] <= cutoff`
            # 在 analysis_date 带时间部分（"2026-07-08 09:30"）或格式不一致时会误分类
            # ——而 holdout 与估计集的分界恰恰决定预注册算不算数。
            kept_est: list[dict] = []
            kept_hold: list[dict] = []
            for r in rows:
                day = _as_date(r.get("analysis_date"))
                if day is None:
                    unparsed_dates += 1
                    kept_est.append(r)   # 判不了期的留在估计集，但如实计数
                elif day > cutoff:
                    kept_hold.append(r)
                else:
                    kept_est.append(r)
            est_rows, hold_rows = kept_est, kept_hold

        out = self._tier_metrics(plan, est_rows, tiers, horizons, n_boot, splits)
        if hold_rows:
            out += [("holdout_" + m, v, s) for m, v, s in
                    self._tier_metrics(plan, hold_rows, tiers, horizons, n_boot, [])]
        context = {
            "rows": len(rows),
            "horizons": horizons,
            "unparsed_dates": unparsed_dates,
        }
        # P1-H2-2：分域覆盖率进 notes——分域均值只有配上"这桶几行"才可解读。
        if "regime" in splits:
            context["regime_known"] = sum(1 for r in rows if r.get("regime"))
        if "size" in splits:
            context["size_known"] = sum(1 for r in rows if r.get("size"))
        if "sector" in splits:
            context["sector_known"] = sum(1 for r in rows if r.get("sector"))
        return out, context

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
                                                     plan.random_seed,
                                                     bonferroni_confidence(plan))
                split_stats: dict[str, dict] = {}
                for key in splits:
                    bucket: dict[str, list[float]] = {}
                    for r in sel:
                        bucket.setdefault(
                            _bucket_label(r.get(key)), []).append(r[suffix])
                    # P1-H2-2：每桶同时给均值和样本数。只给均值，读者无法判断
                    # "17pp 全来自 risk_off"是 22 行还是 2 行撑起来的；桶数放进
                    # splits 本身（而非新增 splits_n 字段），这样旧的 splits_json
                    # 列不用迁移，且均值与 n 不可能被拆到两处各自演化。
                    split_stats[key] = {
                        b: {"mean": round(sum(v) / len(v), 6), "n": len(v)}
                        for b, v in bucket.items()}
                out.append((
                    f"excess_{horizon}_{label}", round(mean, 6),
                    {"n": len(sel), "ci_low": ci_low, "ci_high": ci_high,
                     "hit_rate": round(sum(1 for v in values if v > 0) / len(values), 4),
                     "splits": split_stats}))
            # 净增量（H-002 主判据）：两档均值差，两侧各自按股票分块 bootstrap。
            # 只在同一 horizon 内比较（同 regime 同窗口才构成对照）。
            # net_regime：把对照限定在单一 regime 内（H-002 的"同一市场环境"本意；
            # 不填则全样本——99% 成熟行在 risk_off 时两者数值接近但不义相同）。
            net = plan.params.get("net")
            if net and len(net) == 2:
                la, lb = net
                tier_map = {t[0]: (t[1], t[2]) for t in tiers}
                if la in tier_map and lb in tier_map:
                    net_scope = usable
                    net_regime = plan.params.get("net_regime")
                    if net_regime:
                        net_scope = [r for r in usable
                                     if str(r.get("regime")) == net_regime]
                    def _groups_for(label):
                        lo, hi = tier_map[label]
                        by_group: dict[str, list[float]] = {}
                        for r in net_scope:
                            if _in_tier(r.get("mos_at_entry"), lo, hi):
                                by_group.setdefault(r["stock_code"], []).append(r[suffix])
                        return by_group
                    ga, gb = _groups_for(la), _groups_for(lb)
                    if ga and gb:
                        va = [v for vs in ga.values() for v in vs]
                        vb = [v for vs in gb.values() for v in vs]
                        diff = sum(va) / len(va) - sum(vb) / len(vb)
                        d_lo, d_hi = _block_bootstrap_diff_ci(
                            ga, gb, n_boot, plan.random_seed,
                            bonferroni_confidence(plan))
                        tag = f"net_{horizon}_{la}_vs_{lb}"
                        if net_regime:
                            tag += f"_{net_regime}"
                        out.append((
                            tag, round(diff, 6),
                            {"n": len(va) + len(vb), "ci_low": d_lo, "ci_high": d_hi,
                             "splits": {}}))
        return out


def _as_date(value):
    """analysis_date → date；脏值/空值返回 None（由调用方决定归哪边，不静默消失）。"""
    from datetime import date as _date
    try:
        return _date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _bucket_label(value) -> str:
    """分域桶名：缺值一律叫 `unknown`，不再变成字符串 "None" 混在桶里。

    审计 P1-H2-2：旧写法 `str(r.get(key))` 把 market_state 回填缺口产生的 None
    变成名为 "None" 的桶，报告层很容易整桶忽略——而 E-H001 的 risk_off 集中度
    正是被这种"看不见的桶"放大的（缺标签的行被算进别处或直接消失）。
    """
    if value is None or value == "":
        return "unknown"
    return str(value)


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
        mark_result_verdicts(plan, outcome)
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
        if horizon > step:
            # 窗口 [i+1, i+1+horizon] 以 step 为步长推进；horizon > step 时相邻窗口
            # 持有期重叠，而 total_return 仍按复利累乘（下方 rets 逐窗连乘），
            # 同一段行情的收益会被重复计入，累计收益虚高（审计 N18）。
            # 修法取 fail-fast：重叠窗口的资金分配（分批建仓/仓位拆分）尚未实现，
            # 与其静默产出高估数字，不如拒绝该配置。需要支持请先补实现，勿放宽本校验。
            raise ExperimentError(
                f"horizon_days({horizon}) 不可大于 rebalance_days({step})："
                "调仓窗口重叠时的资金分配未实现，复利累乘会虚高累计收益")
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
        _persist(dao, plan, outcome, manifest)
        return outcome

    outcome = runner.run(plan, manifest)
    _persist(dao, plan, outcome, manifest)
    return outcome


def _persist(dao: ResearchDAO, plan: ExperimentPlan,
             outcome: ExperimentOutcome,
             manifest: SnapshotManifest | None = None) -> None:
    notes = outcome.notes
    if outcome.verdict == "inconclusive":
        reason = inconclusive_reason(plan, outcome)
        if reason:
            notes = f"{notes}；缺证据：{reason}" if notes else f"缺证据：{reason}"
    # 产线实况：E-H001/E-H001b 两行的 code_commit/config_hash 都是空串，
    # 而它们绑定的快照 manifest 里写着 2b2b13b / 336312fe——锚点本来就有，
    # 只是计划没填、也没从快照抄过来，冻结的实验行因此说不出"哪个版本跑出来的"。
    # 计划本身不改（预注册不可篡改），只补实验行；显式填过的值一律尊重。
    commit = plan.code_commit or (manifest.code_commit if manifest else "") or ""
    cfg = plan.config_hash or (manifest.config_hash if manifest else "") or ""
    dao.insert_experiment(Experiment(
        id=plan.experiment_id,
        hypothesis_id=plan.hypothesis_id,
        plan=plan.to_dict(),
        data_snapshot_id=plan.data_snapshot_id,
        code_commit=commit,
        config_hash=cfg,
        random_seed=plan.random_seed,
        status=outcome.status,
        started_at=outcome.started_at,
        finished_at=outcome.finished_at,
        error=outcome.error,
        verdict=outcome.verdict,
        notes=notes,
    ))
    for result in outcome.results:
        dao.insert_result(result)


# ── 计划 CLI（R2-2 预注册）──────────────────────────────────────────────────

def _parse_json_arg(text: str, name: str) -> dict:
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ExperimentError(f"{name} JSON 非法: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    """实验计划 CLI：create（预注册 planned）/ view / list。

    预注册纪律：create 即绑定假设+快照+指标+分域+费用并落 planned；
    同 ID 拒绝覆盖（事后不可篡改）；假设不存在、快照缺失/漂移、阈值
    无法解析一律注册时即拒绝（fail-fast，不留到运行时才爆）。
    """
    parser = argparse.ArgumentParser(prog="research.experiment",
                                     description="Research OS 实验计划（R2-2）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="预注册实验计划（planned）")
    p_create.add_argument("--hypothesis", required=True, help="绑定的假设 ID")
    p_create.add_argument("--snapshot", required=True, help="data_snapshot_id")
    p_create.add_argument("--dataset", required=True, help="数据集（质量门对象）")
    p_create.add_argument("--universe", default="A")
    p_create.add_argument("--benchmark", default="sh000300")
    p_create.add_argument("--metric", required=True, help="主指标名")
    p_create.add_argument("--threshold", required=True, help='判定阈值，如 ">0"')
    p_create.add_argument("--ci-rule", default=None, choices=sorted(CI_RULES),
                          help="CI 判据（预注册）：none=只看轴估计（默认）；"
                               "exclude_zero=CI 不覆盖 0 才判 supported")
    p_create.add_argument("--horizon-days", type=int, required=True)
    p_create.add_argument("--regimes", default="all",
                          help="分域，逗号分隔（如 risk_on,neutral）")
    p_create.add_argument("--costs", default="{}", help='费用 JSON，如 {"slippage":0.001}')
    p_create.add_argument("--params", default="{}",
                          help='参数 JSON（含 start_date/end_date 等）')
    p_create.add_argument("--seed", type=int, default=42)
    p_create.add_argument("--code-commit", default="", help="代码版本（可复现锚点）")
    p_create.add_argument("--config-hash", default="", help="配置哈希（可复现锚点）")
    p_create.add_argument("--id", default=None, help="指定实验 ID（缺省自动编号）")

    p_view = sub.add_parser("view", help="查看计划")
    p_view.add_argument("experiment_id")

    p_list = sub.add_parser("list", help="列出计划")
    p_list.add_argument("--hypothesis", default=None)
    p_list.add_argument("--status", default=None)

    p_run = sub.add_parser("run", help="执行已预注册计划（planned → done/failed/invalid）")
    p_run.add_argument("experiment_id")
    p_run.add_argument("--runner", default=None,
                       choices=["outcome_event_study", "topk_backtest"],
                       help="缺省按 dataset 推断（analysis_outcome→outcome_event_study）")
    p_run.add_argument("--thresholds", default=None,
                       help="质量门阈值覆盖 JSON（缺省用数据集默认）")

    args = parser.parse_args(argv)
    dao = ResearchDAO()

    if args.cmd == "create":
        # 注册时即校验（fail-fast）：假设存在、快照可验证、阈值可解析
        if dao.get_hypothesis(args.hypothesis) is None:
            raise ExperimentError(f"假设不存在: {args.hypothesis}")
        verify_snapshot(args.snapshot)   # 缺失/漂移抛 SnapshotError
        if _compare(0.0, args.threshold) is None:
            raise ExperimentError(f"threshold 无法解析: {args.threshold}")
        if args.horizon_days <= 0:
            raise ExperimentError("horizon_days 必须 > 0")
        regimes = [x.strip() for x in (args.regimes or "").split(",") if x.strip()]
        if not regimes:
            raise ExperimentError("regimes 不可为空")
        with db_conn() as conn:
            existing = [r["id"] for r in conn.execute(
                "SELECT id FROM research_experiment")]
        eid = args.id or next_id("E", now_cn().strftime("%Y-%m-%d"), existing)
        if eid in existing:
            raise ExperimentError(f"实验已存在（预注册不可覆盖）: {eid}")
        # ci_rule 是预注册字段：显式 --ci-rule 优先，否则继承假设锁定的预注册值
        hyp_row = dao.get_hypothesis(args.hypothesis) or {}
        ci_rule = args.ci_rule or hyp_row.get("ci_rule") or "none"
        plan = ExperimentPlan(
            experiment_id=eid, hypothesis_id=args.hypothesis,
            data_snapshot_id=args.snapshot, dataset=args.dataset,
            universe=args.universe, benchmark=args.benchmark,
            metric=args.metric, threshold=args.threshold, ci_rule=ci_rule,
            horizon_days=args.horizon_days, regimes=regimes,
            costs=_parse_json_arg(args.costs, "costs"),
            random_seed=args.seed,
            # code_commit/config_hash 列 NOT NULL：缺省留空串（"未指定"），不传 None
            # （2026-11-04 实测：None 会让后续 run 的 insert_experiment 撞约束）
            code_commit=args.code_commit or "",
            config_hash=args.config_hash or "",
            params=_parse_json_arg(args.params, "params"),
        )
        dao.insert_experiment(Experiment(
            id=eid, hypothesis_id=args.hypothesis, plan=plan.to_dict(),
            data_snapshot_id=args.snapshot,
            code_commit=plan.code_commit or "", config_hash=plan.config_hash or "",
            random_seed=args.seed, status=ExperimentStatus.PLANNED.value,
        ))
        print(eid)
    elif args.cmd == "view":
        row = dao.get_experiment(args.experiment_id)
        if row is None:
            raise ExperimentError(f"实验不存在: {args.experiment_id}")
        print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "list":
        for row in dao.list_experiments(args.hypothesis, args.status):
            print(f"{row['id']}\t{row['status']}\t"
                  f"{row['hypothesis_id']}\t{row['data_snapshot_id']}")
    elif args.cmd == "run":
        row = dao.get_experiment(args.experiment_id)
        if row is None:
            raise ExperimentError(f"实验不存在: {args.experiment_id}")
        if row["status"] != ExperimentStatus.PLANNED.value:
            raise ExperimentError(
                f"实验 {args.experiment_id} 状态为 {row['status']}，"
                "只有 planned 可执行（预注册计划不可重复跑；"
                "失败/无效请查 outcome 记录）")
        plan = ExperimentPlan(**row["plan"])
        runner_name = args.runner
        if runner_name is None:
            runner_name = ("outcome_event_study"
                           if plan.dataset == "analysis_outcome"
                           else "topk_backtest")
        runner = {"outcome_event_study": OutcomeEventStudyRunner(),
                  "topk_backtest": TopKBacktestRunner()}[runner_name]
        thresholds = (_parse_json_arg(args.thresholds, "thresholds")
                      if args.thresholds else None)
        outcome = run_experiment(runner, plan, dao, thresholds)
        print(json.dumps(outcome.to_dict(), ensure_ascii=False,
                         indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
