"""Research OS 实验接口回归测试（R0-6）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "e.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


DATES = [
    "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
    "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16",
    "2026-01-19", "2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23",
    "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30",
]
TRENDS = {"A": 0.02, "B": 0.01, "C": 0.005}
SNAPSHOT = "DS-TEST-E1"
HYPOTHESIS = "H-20261026-001"
EXPERIMENT = "E-20261026-001"


def _seed():
    dao = db_mod.KlineDAO()
    for code, trend in TRENDS.items():
        rows = []
        for i, day in enumerate(DATES):
            close = 100.0 * (1 + trend) ** i
            rows.append({"trade_date": day, "open": close, "close": close,
                         "high": close, "low": close, "volume": 1.0,
                         "amount": 1.0, "turnover": 0.0, "close_adj": close})
        dao.upsert_many(code, rows)
    from src.research.dao import ResearchDAO
    research = ResearchDAO()
    for day in DATES:
        for code, trend in TRENDS.items():
            research.insert_feature_value(f"CN:{code}", day, "F-TREND", 1,
                                          trend, "ok")


def _hypothesis():
    from src.research.dao import ResearchDAO
    from src.research.domain import Hypothesis
    ResearchDAO().insert_hypothesis(Hypothesis(
        id=HYPOTHESIS, version=1, prior="value", statement="趋势有预测力",
        universe="A", signal={"feature": "F-TREND"}, horizon_days=5,
        benchmark="equal_weight", metric="mean_excess", threshold=">0",
        regimes=["all"], costs={}, kill_criteria=["k"], status="locked"))


def _snapshot():
    from src.research.snapshot import create_snapshot
    create_snapshot(["kline_daily", "stock_feature_daily"],
                    snapshot_id=SNAPSHOT, random_seed=42)


def _quality(coverage=1.0, missing=0.0, outliers=0, pit=0):
    from src.research.dao import ResearchDAO
    ResearchDAO().insert_quality({
        "dataset": "kline_daily", "trade_date": DATES[-1], "coverage": coverage,
        "missing_rate": missing, "outliers": outliers, "pit_violations": pit,
        "status": "recorded", "details": {}})


def _plan(**over):
    from src.research.experiment import ExperimentPlan
    data = dict(
        experiment_id=EXPERIMENT, hypothesis_id=HYPOTHESIS,
        data_snapshot_id=SNAPSHOT, dataset="kline_daily", universe="A",
        benchmark="equal_weight", metric="mean_excess", threshold=">0",
        horizon_days=5, regimes=["all"],
        costs={"commission": 0.0, "slippage": 0.0}, random_seed=42,
        code_commit="abc", config_hash="cfg",
        params={"feature_id": "F-TREND", "feature_version": 1,
                "start_date": DATES[0], "end_date": DATES[-1],
                "topk": 1, "rebalance_days": 5})
    data.update(over)
    return ExperimentPlan(**data)


def _result(outcome, metric):
    for r in outcome.results:
        if r.metric == metric:
            return r
    raise AssertionError(f"缺少指标: {metric}")


def _ready(**over):
    _seed()
    _hypothesis()
    _snapshot()
    _quality()
    return _plan(**over)


def test_run_experiment_reproducible(db):
    from src.research.experiment import TopKBacktestRunner, run_experiment
    plan = _ready()
    first = run_experiment(TopKBacktestRunner(), plan)
    second = run_experiment(TopKBacktestRunner(), _plan())
    assert first.status == "done"
    assert first.verdict == "supported"
    assert first.metric_values() == second.metric_values()
    assert (_result(first, "mean_excess").splits["rebalances"]
            == _result(second, "mean_excess").splits["rebalances"])
    assert _result(first, "n_rebalances").value == 3
    assert _result(first, "mean_excess").value > 0
    # 已落库
    from src.research.dao import ResearchDAO
    assert ResearchDAO().get_experiment(EXPERIMENT)["status"] == "done"


def test_quality_invalid_blocks_experiment(db):
    from src.research.experiment import TopKBacktestRunner, run_experiment
    _seed()
    _hypothesis()
    _snapshot()
    _quality(coverage=0.5)
    outcome = run_experiment(TopKBacktestRunner(), _plan())
    assert outcome.status == "invalid"
    assert outcome.verdict == "invalid"
    assert "质量门" in outcome.notes


def test_missing_snapshot_rejected(db):
    from src.research.experiment import TopKBacktestRunner, run_experiment
    from src.research.snapshot import SnapshotError
    _seed()
    _hypothesis()
    with pytest.raises(SnapshotError):
        run_experiment(TopKBacktestRunner(),
                       _plan(data_snapshot_id="DS-NOT-EXIST"))


def test_missing_hypothesis_rejected(db):
    from src.research.experiment import (
        ExperimentError, TopKBacktestRunner, run_experiment,
    )
    _seed()
    _snapshot()
    _quality()
    with pytest.raises(ExperimentError):
        run_experiment(TopKBacktestRunner(),
                       _plan(hypothesis_id="H-NOT-EXIST"))


def test_runner_failure_is_failed_status(db):
    from src.research.experiment import TopKBacktestRunner, run_experiment
    plan = _ready(params={"feature_id": "", "start_date": DATES[0],
                          "end_date": DATES[-1], "topk": 1,
                          "rebalance_days": 5})
    outcome = run_experiment(TopKBacktestRunner(), plan)
    assert outcome.status == "failed"
    assert "feature_id" in outcome.error


def test_verdict_rejected_and_inconclusive(db):
    from src.research.experiment import (
        ExperimentOutcome, TopKBacktestRunner, derive_verdict, run_experiment,
    )
    rejected = run_experiment(TopKBacktestRunner(), _ready(threshold="<0"))
    assert rejected.verdict == "rejected"
    inconclusive = run_experiment(
        TopKBacktestRunner(), _ready(metric="not_a_metric", threshold=">0"))
    assert inconclusive.verdict == "inconclusive"
    empty = ExperimentOutcome(experiment_id=EXPERIMENT, status="done")
    assert derive_verdict(_plan(), empty) == "inconclusive"


def test_result_ids_are_parseable(db):
    """结果 ID 必须符合实体 ID 规则（R-YYYYMMDD-NNN）。"""
    from src.research.domain import parse_id
    from src.research.experiment import TopKBacktestRunner, run_experiment
    _ready()
    outcome = run_experiment(TopKBacktestRunner(), _plan())
    assert outcome.results
    for result in outcome.results:
        prefix, day, seq = parse_id(result.id)
        assert prefix == "R"
        assert len(day) == 8 and seq >= 1


# ── N17：未计算的统计量必须存 NULL（2026-10-28）─────────────────────────

def test_experiment_result_ci_and_p_defaults_are_none():
    """回归 N17：数据类默认值不能再是 0。

    回测路径（TopKBacktestRunner）不跑 bootstrap、不做检验，构造
    ExperimentResult 时压根不传 ci_*/p_value——旧默认 0.0 给**每条**回测
    结果盖上「CI 精确到 [0,0]」与「p=0 极显著」两个假象。
    """
    from src.research.domain import ExperimentResult
    r = ExperimentResult(id="R-20261028-001", experiment_id="E-1",
                         metric="mean_excess", value=1.0)
    assert (r.ci_low, r.ci_high, r.p_value) == (None, None, None)


def test_backtest_results_persist_null_ci_and_pvalue(db):
    """落库同样必须是 NULL，不是 0.0。"""
    from src.research.dao import ResearchDAO
    from src.research.experiment import TopKBacktestRunner, run_experiment
    _ready()
    outcome = run_experiment(TopKBacktestRunner(), _plan())
    assert outcome.status == "done" and outcome.results
    for r in outcome.results:
        assert r.ci_low is None and r.ci_high is None and r.p_value is None
    for row in ResearchDAO().list_results(EXPERIMENT):
        assert row["ci_low"] is None, f"{row['metric']} 的缺失 CI 被写成 0"
        assert row["ci_high"] is None
        assert row["p_value"] is None, "p=0.0 会被读成极显著"


# ── N18：调仓窗口重叠不得复利累乘（2026-10-28）──────────────────────────

def test_overlapping_rebalance_windows_rejected(db):
    """horizon_days > rebalance_days 时相邻窗口持有期重叠。

    旧实现仍逐窗 `total *= (1 + ret)`，同一段行情被重复计入 → 累计收益虚高。
    修法取 fail-fast（重叠窗口的资金分配未实现），故该配置必须被拒绝并说清原因。
    """
    from src.research.experiment import TopKBacktestRunner, run_experiment
    outcome = run_experiment(
        TopKBacktestRunner(), _ready(horizon_days=10))   # rebalance_days 仍为 5
    assert outcome.status == "failed"
    assert "rebalance_days" in outcome.error, outcome.error


def test_non_overlapping_rebalance_still_runs(db):
    """正控：horizon == rebalance 的既有配置不受新校验影响。"""
    from src.research.experiment import TopKBacktestRunner, run_experiment
    outcome = run_experiment(TopKBacktestRunner(), _ready(horizon_days=5))
    assert outcome.status == "done"


def test_quality_date_guards_missing_dataset_and_end_date(db):
    """回归 D1：计划无 end_date 且数据集不在快照表中时不得崩溃。"""
    from src.research.experiment import _quality_date
    _seed()
    _snapshot()
    plan = _plan()
    plan.dataset = "not_in_snapshot"
    plan.params = {"feature_id": "F-TREND", "start_date": DATES[0],
                   "topk": 1, "rebalance_days": 5}
    from src.research.snapshot import verify_snapshot
    manifest = verify_snapshot(SNAPSHOT)
    assert _quality_date(plan, manifest) == DATES[0]
