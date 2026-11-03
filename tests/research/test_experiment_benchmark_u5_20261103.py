"""U5 复核用例（2026-11-03）：事件研究在 benchmark 缺省时不得混算多个基准。

产线只读取证（交付帖附命令）：`analysis_outcome` 里 sh000300 / sh000922 **各 390 行、
日期区间完全重叠**——同一条分析对两个基准各存一行。旧实现
`_load_outcomes(start, end, plan.benchmark or None)` 对空 benchmark 的计划**不过滤**，
于是样本量按基准数翻倍，并把两种超额口径混进同一个均值与 CI。
这里钉三件事：多基准必须显式指定、单基准可兜底解析、指定后只取那一套。
"""

import pytest

from src.models import database as db_mod

COLS = ("analysis_id, benchmark_code, stock_code, analysis_date, entry_date, "
        "signal, confidence, verdict, entry_price, ret_20, ret_60, ret_120, "
        "bench_20, bench_60, bench_120, excess_20, excess_60, excess_120, "
        "mos_at_entry, updated_at")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "u5.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _insert(rows, benchmark="sh000300", start_id=1):
    with db_mod.db_conn() as conn:
        for i, r in enumerate(rows, start=start_id):
            conn.execute(
                f"INSERT INTO analysis_outcome ({COLS}) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i, benchmark, r["stock"], r["date"], r["date"], "BUY", "高",
                 "通过", 1.0, 0.01, 0.02, None, 0.0, 0.0, None,
                 r["e20"], r["e60"], None, r["mos"], "2026-11-03"))
    return start_id + len(rows)


def _plan(benchmark, seed=42):
    from src.research.experiment import ExperimentPlan
    return ExperimentPlan(
        experiment_id="E-U5-001", hypothesis_id="H-U5",
        data_snapshot_id="DS-U5", dataset="analysis_outcome", universe="A",
        benchmark=benchmark, metric="excess_20_20~40", threshold=">0",
        horizon_days=20, regimes=["all"], costs={}, random_seed=seed,
        code_commit="c", config_hash="h",
        params={"start_date": "2026-07-08", "end_date": "2026-07-09",
                "horizons": [20], "n_boot": 100, "splits": []})


ROWS = [
    {"stock": "A", "date": "2026-07-08", "mos": 30.0, "e20": 0.03, "e60": 0.05},
    {"stock": "B", "date": "2026-07-09", "mos": 30.0, "e20": 0.05, "e60": 0.05},
]


def test_empty_benchmark_with_two_benchmarks_is_refused(db):
    """产线现状（两基准重叠）：空 benchmark 必须报错，不许给混算数字。"""
    from src.research.experiment import ExperimentError, OutcomeEventStudyRunner
    _insert(ROWS, benchmark="sh000300")
    _insert([dict(r, e20=r["e20"] - 0.02) for r in ROWS],
            benchmark="sh000922", start_id=101)
    with pytest.raises(ExperimentError) as ei:
        OutcomeEventStudyRunner()._compute(_plan(""))
    msg = str(ei.value)
    assert "sh000300" in msg and "sh000922" in msg
    assert "2 个基准" in msg


def test_refusal_surfaces_as_failed_outcome_not_a_number(db):
    """runner 层：拒绝要变成 status=failed + 原因，而不是悄悄产出一个可解释的数。"""
    from src.research.experiment import OutcomeEventStudyRunner
    from src.research.snapshot import create_snapshot
    _insert(ROWS, benchmark="sh000300")
    _insert([dict(r, e20=r["e20"] - 0.02) for r in ROWS],
            benchmark="sh000922", start_id=101)
    outcome = OutcomeEventStudyRunner().run(
        _plan(""), create_snapshot(["analysis_outcome"], snapshot_id="DS-U5"))
    assert outcome.status == "failed"
    assert "benchmark" in (outcome.error or "")
    assert outcome.results == []


def test_explicit_benchmark_does_not_double_the_sample(db):
    """指定基准后只取那一套：n=2 而不是 4，均值是那一个基准的数。"""
    from src.research.experiment import OutcomeEventStudyRunner
    from src.research.snapshot import create_snapshot
    _insert(ROWS, benchmark="sh000300")
    _insert([dict(r, e20=r["e20"] - 0.02) for r in ROWS],
            benchmark="sh000922", start_id=101)
    outcome = OutcomeEventStudyRunner().run(
        _plan("sh000300"), create_snapshot(["analysis_outcome"],
                                           snapshot_id="DS-U5"))
    assert outcome.status == "done"
    m = {r.metric: r for r in outcome.results}["excess_20_20~40"]
    assert m.sample_size == 2
    assert m.value == pytest.approx(0.04)          # (0.03+0.05)/2，未被 -0.02/-0.03 稀释


def test_single_benchmark_window_still_resolves_without_plan_field(db):
    """只有一种基准时不苛求计划填字段：兜底解析等价于显式指定。"""
    from src.research.experiment import OutcomeEventStudyRunner, _sole_benchmark
    from src.research.snapshot import create_snapshot
    _insert(ROWS, benchmark="sh000922")
    assert _sole_benchmark("2026-07-08", "2026-07-09") == "sh000922"
    outcome = OutcomeEventStudyRunner().run(
        _plan(""), create_snapshot(["analysis_outcome"], snapshot_id="DS-U5"))
    assert outcome.status == "done"
    m = {r.metric: r for r in outcome.results}["excess_20_20~40"]
    assert m.sample_size == 2 and m.value == pytest.approx(0.04)


def test_empty_window_returns_none_rather_than_guessing(db):
    from src.research.experiment import _sole_benchmark
    assert _sole_benchmark("2026-07-08", "2026-07-09") is None
