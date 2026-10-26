"""事件研究 runner 回归测试（R2-0 修 G1/G3/G4）。"""

import pytest

from src.models import database as db_mod

COLS = ("analysis_id, benchmark_code, stock_code, analysis_date, entry_date, "
        "signal, confidence, verdict, entry_price, ret_20, ret_60, ret_120, "
        "bench_20, bench_60, bench_120, excess_20, excess_60, excess_120, "
        "mos_at_entry, updated_at")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "es.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _insert(rows):
    with db_mod.db_conn() as conn:
        for i, r in enumerate(rows, start=1):
            conn.execute(
                f"INSERT INTO analysis_outcome ({COLS}) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i, "sh000300", r["stock"], r["date"], r["date"], "BUY", "高",
                 "通过", 1.0, 0.01, 0.02, None, 0.0, 0.0, None,
                 r["e20"], r["e60"], None, r["mos"], "2026-10-01"))


def test_block_bootstrap_groups_by_stock():
    from src.research.experiment import block_bootstrap_ci
    values = [0.01] * 5 + [0.05] * 5
    groups = ["A"] * 5 + ["B"] * 5
    lo, hi = block_bootstrap_ci(values, groups, n_boot=200, seed=1)
    assert lo is not None and hi is not None
    assert lo <= 0.03 <= hi
    assert block_bootstrap_ci([], [], n_boot=10) == (None, None)


def test_event_study_tiers_and_ci(db):
    from src.research.experiment import OutcomeEventStudyRunner
    from src.research.snapshot import create_snapshot
    from src.research.experiment import ExperimentPlan
    _insert([
        {"stock": "A", "date": "2026-07-08", "mos": -5.0, "e20": -0.01, "e60": -0.02},
        {"stock": "A", "date": "2026-07-09", "mos": -5.0, "e20": -0.01, "e60": -0.02},
        {"stock": "B", "date": "2026-07-08", "mos": 30.0, "e20": 0.03, "e60": 0.05},
        {"stock": "B", "date": "2026-07-09", "mos": 30.0, "e20": 0.03, "e60": 0.05},
    ])
    create_snapshot(["analysis_outcome"], snapshot_id="DS-ES")
    plan = ExperimentPlan(
        experiment_id="E-20261026-001", hypothesis_id="H-1",
        data_snapshot_id="DS-ES", dataset="analysis_outcome", universe="A",
        benchmark="sh000300", metric="excess_20_20~40", threshold=">0",
        horizon_days=20, regimes=["all"], costs={}, random_seed=42,
        code_commit="c", config_hash="h",
        params={"start_date": "2026-07-08", "end_date": "2026-07-09",
                "horizons": [20], "n_boot": 100, "splits": []})
    outcome = OutcomeEventStudyRunner().run(plan, create_snapshot(
        ["analysis_outcome"], snapshot_id="DS-ES"))
    assert outcome.status == "done"
    metrics = {r.metric: r for r in outcome.results}
    assert metrics["excess_20_20~40"].value == 0.03
    assert metrics["excess_20_<=0"].value == -0.01
    assert metrics["excess_20_20~40"].ci_low is not None
    assert metrics["excess_20_20~40"].sample_size == 2


def test_event_study_splits(db):
    from src.research.experiment import OutcomeEventStudyRunner
    from src.research.experiment import ExperimentPlan
    from src.research.snapshot import create_snapshot
    _insert([
        {"stock": "A", "date": "2026-07-08", "mos": 30.0, "e20": 0.03, "e60": 0.05},
        {"stock": "B", "date": "2026-07-08", "mos": 30.0, "e20": 0.01, "e60": 0.02},
    ])
    db_mod.StockSnapshotDAO().save_batch([
        {"code": "A", "name": "A", "market": "A", "sector": "白酒", "pe": 20.0,
         "pb": 3.0, "ps": 1.0, "market_cap": 2e10, "circulating_cap": 2e10,
         "roe": 10.0, "revenue": 1.0, "revenue_growth": 5.0, "profit": 1.0,
         "profit_growth": 5.0, "debt_ratio": 30.0, "current_price": 1.0,
         "high_52w": 1.0, "low_52w": 1.0, "is_st": False,
         "list_date": "2010-01-01", "snapshot_date": "2026-07-08"},
        {"code": "B", "name": "B", "market": "A", "sector": "银行", "pe": 8.0,
         "pb": 0.8, "ps": 1.0, "market_cap": 5e8, "circulating_cap": 5e8,
         "roe": 10.0, "revenue": 1.0, "revenue_growth": 5.0, "profit": 1.0,
         "profit_growth": 5.0, "debt_ratio": 90.0, "current_price": 1.0,
         "high_52w": 1.0, "low_52w": 1.0, "is_st": False,
         "list_date": "2010-01-01", "snapshot_date": "2026-07-08"},
    ])
    create_snapshot(["analysis_outcome"], snapshot_id="DS-ES2")
    plan = ExperimentPlan(
        experiment_id="E-2", hypothesis_id="H-1", data_snapshot_id="DS-ES2",
        dataset="analysis_outcome", universe="A", benchmark="sh000300",
        metric="excess_20_20~40", threshold=">0", horizon_days=20,
        regimes=["all"], costs={}, random_seed=42, code_commit="c",
        config_hash="h",
        params={"start_date": "2026-07-08", "end_date": "2026-07-08",
                "horizons": [20], "n_boot": 50, "splits": ["sector", "size"]})
    manifest = create_snapshot(["analysis_outcome"], snapshot_id="DS-ES2")
    outcome = OutcomeEventStudyRunner().run(plan, manifest)
    splits = {r.metric: r for r in outcome.results}["excess_20_20~40"].splits
    assert set(splits["sector"]) == {"白酒", "银行"}
    assert set(splits["size"]) == {"large", "small"}
