"""净增量指标（H-002 主判据，2026-11-05 母方实现）。

同 horizon 内两档均值差（net），两侧各自按股票分块 bootstrap 出 CI：
- 只有两档都有样本才产出（缺一档不硬算 0）；
- CI 覆盖 0 即"无区分度"（预注册 ci_rule=exclude_zero 的判据基础）。
"""

import pytest

from src.models import database as db_mod

COLS = ("analysis_id, benchmark_code, stock_code, analysis_date, entry_date, "
        "signal, confidence, verdict, entry_price, ret_20, ret_60, ret_120, "
        "bench_20, bench_60, bench_120, excess_20, excess_60, excess_120, "
        "mos_at_entry, updated_at")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "net.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _insert(rows, benchmark="sh000300", start_id=1):
    """rows: (stock, analysis_date, mos, e60)。"""
    with db_mod.db_conn() as conn:
        for i, (stock, day, mos, e60) in enumerate(rows, start=start_id):
            conn.execute(
                f"INSERT INTO analysis_outcome ({COLS}) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i, benchmark, stock, day, day, "BUY", "高", "通过", 1.0,
                 0.01, 0.02, None, 0.0, 0.0, None, None, e60, None, mos,
                 "2026-11-05"))


def _plan(net=(">=40", "<=0"), horizons=(60,)):
    from src.research.experiment import ExperimentPlan
    return ExperimentPlan(
        experiment_id="E-NET", hypothesis_id="H-002", data_snapshot_id="DS-NET",
        dataset="analysis_outcome", universe="A", benchmark="sh000300",
        metric="net_60_>=40_vs_<=0", threshold=">0", horizon_days=60,
        regimes=["all"], costs={}, random_seed=42, code_commit="c",
        config_hash="h",
        params={"start_date": "2026-07-08", "end_date": "2026-07-08",
                "tiers": [["<=0", None, 0.0], ["0~20", 0.0, 20.0],
                          ["20~40", 20.0, 40.0], [">=40", 40.0, None]],
                "horizons": list(horizons), "n_boot": 200,
                "splits": [], "holdout_days": 0, "net": list(net)})


def _run(plan):
    from src.research.experiment import OutcomeEventStudyRunner
    from src.research.snapshot import create_snapshot
    return OutcomeEventStudyRunner().run(
        plan, create_snapshot(["analysis_outcome"], snapshot_id="DS-NET"))


def _net(outcome):
    return [r for r in outcome.results if r.metric == "net_60_>=40_vs_<=0"]


def test_net_emitted_with_diff_and_ci(db):
    """两档各 4 只：>=40 档 e60=10、<=0 档 e60=4 → 净增量 6，CI 覆盖 6。"""
    rows = [(f"A{i}", "2026-07-08", 50.0, 10.0) for i in range(4)]
    rows += [(f"B{i}", "2026-07-08", -5.0, 4.0) for i in range(4)]
    _insert(rows)
    outcome = _run(_plan())
    net = _net(outcome)
    assert len(net) == 1
    r = net[0]
    assert r.value == pytest.approx(6.0)
    assert r.sample_size == 8
    assert r.ci_low is not None and r.ci_high is not None
    assert r.ci_low <= 6.0 <= r.ci_high


def test_net_missing_tier_not_emitted(db):
    """只有一档有样本 → 不产出净增量（不硬算 0）。"""
    _insert([(f"A{i}", "2026-07-08", 50.0, 10.0) for i in range(4)])
    outcome = _run(_plan())
    assert _net(outcome) == []


def test_net_identical_tiers_ci_covers_zero(db):
    """两档同分布 → 净增量 0、CI 覆盖 0（无区分度的样子）。"""
    rows = [(f"A{i}", "2026-07-08", 50.0, 5.0) for i in range(4)]
    rows += [(f"B{i}", "2026-07-08", -5.0, 5.0) for i in range(4)]
    _insert(rows)
    outcome = _run(_plan())
    r = _net(outcome)[0]
    assert r.value == pytest.approx(0.0)
    assert r.ci_low <= 0.0 <= r.ci_high


def test_net_regime_conditions_the_comparison(db):
    """net_regime：对照限定在单一 regime 内（H-002 的"同一市场环境"本意）。

    risk_off 日：>=40 档 e60=10、<=0 档 e60=4 → 净 6；
    neutral 日：两档都 0 → 不条件化时全样本净 = (10+0)/2-(4+0)/2 = 3。
    """
    rows = [("A0", "2026-07-08", 50.0, 10.0), ("A1", "2026-07-08", 50.0, 10.0),
            ("B0", "2026-07-08", -5.0, 4.0), ("B1", "2026-07-08", -5.0, 4.0)]
    rows += [("C0", "2026-07-09", 50.0, 0.0), ("C1", "2026-07-09", 50.0, 0.0),
             ("D0", "2026-07-09", -5.0, 0.0), ("D1", "2026-07-09", -5.0, 0.0)]
    _insert(rows)
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO market_state_daily (trade_date, status, regime,"
                     " created_at) VALUES ('2026-07-08','pass','risk_off',"
                     "'2026-11-05T00:00:00')")
        conn.execute("INSERT INTO market_state_daily (trade_date, status, regime,"
                     " created_at) VALUES ('2026-07-09','pass','neutral',"
                     "'2026-11-05T00:00:00')")
    plan = _plan()
    plan.params["net_regime"] = "risk_off"
    outcome = _run(plan)
    r = _net(outcome)[0]
    assert r.metric == "net_60_>=40_vs_<=0_risk_off"
    assert r.value == pytest.approx(6.0)
    assert r.sample_size == 4

