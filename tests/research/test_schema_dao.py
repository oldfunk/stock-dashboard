"""Research OS schema/DAO 回归测试（R0-2）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "r.db"))
    db_mod.init_database()
    return db_mod


def _tables():
    with db_mod.db_conn() as conn:
        return {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}


def test_research_tables_created_and_idempotent(db):
    expected = {
        "market", "instrument", "trading_calendar", "fx_rate_daily",
        "feature_definition", "stock_feature_daily", "universe_membership",
        "data_quality_daily", "research_hypothesis", "research_experiment",
        "research_result", "strategy_version", "strategy_deployment",
        "strategy_attribution", "decision_log", "prompt_registry", "llm_run",
    }
    assert expected <= _tables()
    # 二次初始化不报错、不丢表
    db_mod.init_database()
    assert expected <= _tables()
    with db_mod.db_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM market WHERE market_id='CN'").fetchone()[0] == 1


def test_kline_has_market_dimension_columns(db):
    with db_mod.db_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(kline_daily)")}
    assert {"market", "instrument_id"} <= cols


def test_hypothesis_experiment_result_roundtrip(db):
    from src.research.dao import ResearchDAO
    from src.research.domain import (
        Experiment, ExperimentResult, Hypothesis, HypothesisStatus,
    )
    dao = ResearchDAO()
    h = Hypothesis(
        id="H-20261026-001", version=1, prior="value",
        statement="测试假设", universe="A股", signal={"rank": "mos"},
        horizon_days=126, benchmark="sh000300", metric="excess",
        threshold=">0", regimes=["all"], costs={"commission": 0.00025},
        kill_criteria=["超额连续两年为负"],
        status=HypothesisStatus.LOCKED.value,
    )
    dao.insert_hypothesis(h)
    got = dao.get_hypothesis(h.id)
    assert got["statement"] == "测试假设"
    assert got["signal"] == {"rank": "mos"}
    assert got["regimes"] == ["all"]
    assert dao.list_hypotheses("locked")[0]["id"] == h.id

    e = Experiment(
        id="E-20261026-001", hypothesis_id=h.id, plan={"topk": 20},
        data_snapshot_id="DS-20261026", code_commit="abc123",
        config_hash="cfg", random_seed=42, status="planned",
    )
    dao.insert_experiment(e)
    assert dao.get_experiment(e.id)["plan"] == {"topk": 20}

    r = ExperimentResult(
        id="R-20261026-001", experiment_id=e.id, metric="excess_return",
        value=0.021, ci_low=0.005, ci_high=0.038, sample_size=120,
        verdict="supported", splits={"regime": {"risk_on": 0.03}},
    )
    dao.insert_result(r)
    assert dao.list_results(e.id)[0]["splits"]["regime"]["risk_on"] == 0.03


def test_strategy_decision_prompt_llm_roundtrip(db):
    from src.research.dao import ResearchDAO
    from src.research.domain import Decision, StrategyVersion
    dao = ResearchDAO()
    s = StrategyVersion(id="S-VALUE-QUALITY-v1", version=1, parent_id="",
                        spec={"topk": 20}, status="draft",
                        provenance={"experiments": ["E-1"]})
    dao.insert_strategy(s)
    assert dao.get_strategy(s.id)["spec"] == {"topk": 20}

    d = Decision(id="DL-20261026-001", decision_date="2026-10-26",
                 type="promote", target=s.id, change="进入纸盘",
                 rationale="实验通过", evidence={"E-1": "supported"},
                 expected="3个月一致", review_date="2027-01-31", status="open")
    dao.insert_decision(d)
    assert dao.list_decisions("open")[0]["evidence"] == {"E-1": "supported"}

    dao.insert_prompt({
        "prompt_id": "market_summary", "version": 1, "task": "market_summary",
        "template_hash": "h", "schema": {"type": "object"},
        "model_tier": "fast", "tools": ["get_market_state"], "max_tokens": 2000,
    })
    assert dao.get_prompt("market_summary", 1)["tools"] == ["get_market_state"]

    dao.insert_llm_run({
        "id": "run-1", "task": "market_summary", "prompt_id": "market_summary",
        "prompt_version": 1, "model": "glm-4.5-flash", "input_hash": "x",
        "output": {"summary": "ok"}, "schema_ok": 1, "citations_ok": 1,
        "tokens": 100, "latency_ms": 500,
    })
    assert dao.list_llm_runs("market_summary")[0]["output"] == {"summary": "ok"}
