"""Research OS 测试夹具（R0-12）：最小数据集 + 实验 fixture + 假 LLM/时钟。

全部离线，不触网、不要 Key；供 tests/research/* 复用。
"""

import pytest

from src.models import database as db_mod

DATES = [
    "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
    "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16",
    "2026-01-19", "2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23",
    "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30",
]
TRENDS = {"A": 0.02, "B": 0.01, "C": 0.005}
SNAPSHOT = "DS-FIXTURE"


@pytest.fixture
def minimal_db(tmp_path, monkeypatch):
    """最小隔离库（研究表 + 业务表 schema）。"""
    path = tmp_path / "data" / "db" / "minimal.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


@pytest.fixture
def experiment_fixture(minimal_db):
    """最小可跑实验：3 只品种 × 20 交易日 + 特征 + 快照 + 质量 + 假设 + 计划。"""
    from src.research.dao import ResearchDAO
    from src.research.domain import Hypothesis
    from src.research.experiment import ExperimentPlan
    from src.research.snapshot import create_snapshot

    dao = db_mod.KlineDAO()
    for code, trend in TRENDS.items():
        rows = []
        for i, day in enumerate(DATES):
            close = 100.0 * (1 + trend) ** i
            rows.append({"trade_date": day, "open": close, "close": close,
                         "high": close, "low": close, "volume": 1.0,
                         "amount": 1.0, "turnover": 0.0, "close_adj": close})
        dao.upsert_many(code, rows)

    research = ResearchDAO()
    for day in DATES:
        for code, trend in TRENDS.items():
            research.insert_feature_value(f"CN:{code}", day, "F-TREND", 1,
                                          trend, "ok")
    research.insert_hypothesis(Hypothesis(
        id="H-20261026-001", version=1, prior="value", statement="趋势有预测力",
        universe="A", signal={"feature": "F-TREND"}, horizon_days=5,
        benchmark="equal_weight", metric="mean_excess", threshold=">0",
        regimes=["all"], costs={}, kill_criteria=["k"], status="locked"))
    research.insert_quality({
        "dataset": "kline_daily", "trade_date": DATES[-1], "coverage": 1.0,
        "missing_rate": 0.0, "outliers": 0, "pit_violations": 0,
        "status": "recorded", "details": {}})
    create_snapshot(["kline_daily", "stock_feature_daily"],
                    snapshot_id=SNAPSHOT, random_seed=42)
    plan = ExperimentPlan(
        experiment_id="E-20261026-001", hypothesis_id="H-20261026-001",
        data_snapshot_id=SNAPSHOT, dataset="kline_daily", universe="A",
        benchmark="equal_weight", metric="mean_excess", threshold=">0",
        horizon_days=5, regimes=["all"], costs={}, random_seed=42,
        code_commit="abc", config_hash="cfg",
        params={"feature_id": "F-TREND", "feature_version": 1,
                "start_date": DATES[0], "end_date": DATES[-1],
                "topk": 1, "rebalance_days": 5})
    return {"plan": plan, "snapshot": SNAPSHOT, "dates": DATES,
            "dao": research, "klines": db_mod.KlineDAO()}


@pytest.fixture
def fake_llm():
    from src.research.llm.fake import FakeLLM
    return FakeLLM()


@pytest.fixture
def fixed_clock():
    from src.research.llm.fake import FakeClock
    return FakeClock()
