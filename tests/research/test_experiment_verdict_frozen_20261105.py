"""批17-b 用例（2026-11-05）：R2-5 结论与冻结 —— verdict/notes 必须落进实验行。

产线只读实况（2 个实验、16 条 result）：
- `research_experiment` **没有** verdict/notes 列，`_persist` 也从不传；
  `derive_verdict` 算出来的 supported 只存在于当时的 CLI 输出，冻结的实验行里查不到；
- `research_result.verdict` 16 行全是空串（runner 不给每条指标判结论）；
- `outcome.notes` 里那一大段诚实限定（幸存者偏差、分域覆盖率、多重比较提示）同样不落库。
所以"终审 H-001"到现在没有可查的冻结结论——本批补的就是这一格。
"""

import pytest

from src.models import database as db_mod

COLS = ("analysis_id, benchmark_code, stock_code, analysis_date, entry_date, "
        "signal, confidence, verdict, entry_price, ret_20, ret_60, ret_120, "
        "bench_20, bench_60, bench_120, excess_20, excess_60, excess_120, "
        "mos_at_entry, updated_at")
SNAPSHOT = "DS-TEST-V1"
HYPOTHESIS = "H-20261105-001"
EXPERIMENT = "E-20261105-001"
QDATE = "2026-07-08"


class _ResultRowStub:
    """归因函数只读 metric/value 两个属性，给个最小结果行就够（不建库也能测）。"""

    def __init__(self, metric: str, value: float):
        self.metric = metric
        self.value = value


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "v.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _seed_outcomes(rows=(("A", 30.0, 3.0, 5.0), ("B", 35.0, 4.0, 6.0))):
    with db_mod.db_conn() as conn:
        for i, (code, mos, e20, e60) in enumerate(rows, start=1):
            conn.execute(
                f"INSERT INTO analysis_outcome ({COLS}) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i, "sh000300", code, QDATE, QDATE, "BUY", "高", "通过", 1.0,
                 0.01, 0.02, None, 0.0, 0.0, None, e20, e60, None, mos,
                 "2026-11-05"))


def _hypothesis():
    from src.research.dao import ResearchDAO
    from src.research.domain import Hypothesis
    ResearchDAO().insert_hypothesis(Hypothesis(
        id=HYPOTHESIS, version=1, prior="value", statement="MOS 越高超额越高",
        universe="A", signal={"feature": "mos"}, horizon_days=60,
        benchmark="sh000300", metric="excess_20_20~40", threshold=">0",
        regimes=["all"], costs={}, kill_criteria=["中间档持续反向"], status="locked"))


def _snapshot():
    from src.research.snapshot import create_snapshot
    create_snapshot(["analysis_outcome"], snapshot_id=SNAPSHOT, random_seed=42)


def _quality(coverage=1.0):
    from src.research.dao import ResearchDAO
    ResearchDAO().insert_quality({
        "dataset": "analysis_outcome", "trade_date": QDATE, "coverage": coverage,
        "missing_rate": 0.0, "outliers": 0, "pit_violations": 0,
        "status": "recorded", "details": {}})


def _plan(**over):
    from src.research.experiment import ExperimentPlan
    data = dict(
        experiment_id=EXPERIMENT, hypothesis_id=HYPOTHESIS,
        data_snapshot_id=SNAPSHOT, dataset="analysis_outcome", universe="A",
        benchmark="sh000300", metric="excess_20_20~40", threshold=">0",
        horizon_days=20, regimes=["all"], costs={}, random_seed=42,
        code_commit="abc", config_hash="cfg",
        params={"start_date": "2026-07-08", "end_date": QDATE,
                "horizons": [20], "n_boot": 50, "splits": []})
    data.update(over)
    return ExperimentPlan(**data)


def _run(**plan_over):
    from src.research.experiment import OutcomeEventStudyRunner, run_experiment
    _snapshot()                       # 快照指纹要在数据就位后再算
    return run_experiment(OutcomeEventStudyRunner(), _plan(**plan_over))


def _row():
    from src.research.dao import ResearchDAO
    return ResearchDAO().get_experiment(EXPERIMENT)


# ── 落库：结论 + 限定语 ───────────────────────────────────

def test_supported_verdict_is_frozen_on_the_experiment_row(db):
    _seed_outcomes()
    _hypothesis()
    _quality()
    outcome = _run()
    assert outcome.verdict == "supported"
    row = _row()
    assert row["verdict"] == "supported", "结论必须落在冻结的实验行里，不能只在 CLI 输出"
    assert row["status"] == "done"


def test_honest_qualifications_survive_persistence(db):
    """批16-b 写的那段限定语（不是同一集合 / 多重比较）现在真的进库了。"""
    _seed_outcomes()
    _hypothesis()
    _quality()
    _run()
    notes = _row()["notes"]
    assert notes and "不是同一集合" in notes and "多重比较" in notes, \
        "notes 不落库 = 免责说明随进程消失，事后审计只能靠手抄"


def test_rejected_direction_is_persisted_too(db):
    _seed_outcomes(rows=(("A", 30.0, -3.0, -5.0), ("B", 35.0, -4.0, -6.0)))
    _hypothesis()
    _quality()
    assert _run().verdict == "rejected"
    assert _row()["verdict"] == "rejected"


def test_invalid_quality_gate_verdict_is_persisted(db):
    """invalid 也是一格结论：库里要能看出是质量门拦的，而不是"跑完了没结论"。"""
    _seed_outcomes()
    _hypothesis()
    _quality(coverage=0.0)            # 覆盖率低于门限 -> invalid
    outcome = _run()
    assert outcome.status == "invalid" and outcome.verdict == "invalid"
    row = _row()
    assert row["verdict"] == "invalid"
    assert "数据质量门未通过" in row["notes"]


# ── inconclusive 必须写清缺哪一类证据 ────────────────────

def test_missing_criterion_metric_records_which_evidence_is_absent(db):
    _seed_outcomes()
    _hypothesis()
    _quality()
    outcome = _run(metric="excess_99_not_a_real_metric")
    assert outcome.verdict == "inconclusive"
    notes = _row()["notes"]
    assert "缺证据：计划判据 excess_99_not_a_real_metric 不在实报的" in notes, \
        "三种 inconclusive 在库里长一个样，事后只能猜"


def test_no_rows_at_all_records_no_result_rows(db):
    """质量门放行但 runner 算不出任何指标：预注册的分档与数据不匹配就是这种形状。

    （空数据集走不到这里——质量门先判 invalid，这是另一格，见上一个用例族。）
    """
    _seed_outcomes()
    _hypothesis()
    _quality()
    params = {"start_date": "2026-07-08", "end_date": QDATE, "horizons": [20],
              "n_boot": 50, "splits": [], "tiers": [["90+", 90.0, None]]}
    outcome = _run(params=params)
    assert outcome.results == [] and outcome.verdict == "inconclusive"
    assert "缺证据：无结果行" in _row()["notes"]


@pytest.mark.parametrize("metric,threshold,expect", [
    ("excess_20_20~40", ">0", ""),                       # 判得动 -> 不该有归因
    ("excess_20_20~40", "高于一些", "不可解析"),           # 阈值不可解析
    ("excess_99_none", ">0", "不在实报的"),               # 判据不在结果集合里
])
def test_inconclusive_reason_covers_the_three_shapes(metric, threshold, expect):
    """归因函数自己也要被测，不然 notes 里那句"缺证据"会随分支漂移失真。"""
    from src.research.experiment import (
        ExperimentOutcome, inconclusive_reason,
    )
    plan = _plan(metric=metric, threshold=threshold)
    outcome = ExperimentOutcome(experiment_id=EXPERIMENT, status="done",
                                results=[], verdict="inconclusive")
    row = _ResultRowStub("excess_20_20~40", 3.0)
    outcome.results = [row]
    got = inconclusive_reason(plan, outcome)
    if expect:
        assert expect in got
    else:
        assert got == "", f"判得动的组合不该给出归因：{got}"


def test_unparseable_threshold_records_the_reason(db):
    _seed_outcomes()
    _hypothesis()
    _quality()
    outcome = _run(threshold="高于一些")
    assert outcome.verdict == "inconclusive"
    assert "阈值" in _row()["notes"] and "不可解析" in _row()["notes"]


# ── schema：新库直接建，老库靠 ALTER（产线是后者）──────────

# ── 可复现锚点：计划没填就从绑定的快照抄（产线两行都空着）──

def test_empty_code_commit_is_inherited_from_the_snapshot(db, monkeypatch):
    """实验行必须说得出"哪个版本跑出来的"，锚点在快照 manifest 里本来就有。"""
    from src.research import snapshot as snap
    monkeypatch.setattr(snap, "code_commit", lambda: "dead0be")
    monkeypatch.setattr(snap, "config_hash", lambda: "cfg-from-snapshot")
    _seed_outcomes()
    _hypothesis()
    _quality()
    _run(code_commit="", config_hash="")     # 计划里两个锚点字段都空着
    row = _row()
    assert row["code_commit"] == "dead0be", \
        "产线 E-H001 两行 code_commit='' 而快照记着 2b2b13b，就是这个缺口"
    assert row["config_hash"] == "cfg-from-snapshot"


def test_explicit_code_commit_on_the_plan_is_not_overwritten(db, monkeypatch):
    """显式填过就尊重显式值——预注册写的是什么就是什么，不能被运行时改掉。"""
    from src.research import snapshot as snap
    monkeypatch.setattr(snap, "code_commit", lambda: "dead0be")
    _seed_outcomes()
    _hypothesis()
    _quality()
    _run(code_commit="abc", config_hash="cfg")
    row = _row()
    assert row["code_commit"] == "abc" and row["config_hash"] == "cfg"
    plan = row["plan"]                     # DAO 已把 plan_json 解析成 dict
    assert isinstance(plan, dict) and plan["code_commit"] == "abc", \
        "落库不许回写计划本身（预注册不可篡改）"


def test_columns_exist_on_a_fresh_database(db):
    with db_mod.db_conn() as conn:
        cols = [r["name"] for r in conn.execute(
            "PRAGMA table_info(research_experiment)").fetchall()]
    assert "verdict" in cols and "notes" in cols


def test_existing_database_gets_the_columns_by_migration(tmp_path, monkeypatch):
    """产线库是先有表后加列——init_database 的 ALTER 路径必须真的有效。"""
    path = tmp_path / "data" / "db" / "legacy.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    import sqlite3
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "CREATE TABLE research_experiment ("
            " id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL, plan_json TEXT,"
            " data_snapshot_id TEXT NOT NULL, code_commit TEXT NOT NULL,"
            " config_hash TEXT NOT NULL, random_seed INTEGER, status TEXT NOT NULL,"
            " started_at TEXT, finished_at TEXT, error TEXT, created_at TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO research_experiment (id, hypothesis_id, data_snapshot_id,"
            " code_commit, config_hash, status, created_at)"
            " VALUES ('E-OLD','H-OLD','DS','abc','cfg','done','2026-11-04')")
        conn.commit()
    db_mod.init_database()
    with db_mod.db_conn() as conn:
        row = conn.execute("SELECT * FROM research_experiment WHERE id='E-OLD'").fetchone()
        assert row["verdict"] is None, \
            "迁移前的实验行没有当时的结论，NULL 比空串诚实（不许补成一条假的未判定）"
        assert row["notes"] is None
