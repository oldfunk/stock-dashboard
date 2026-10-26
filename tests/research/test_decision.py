"""Research OS 决策账本回归测试（R0-7）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "d.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _seed():
    from src.research.dao import ResearchDAO
    from src.research.domain import Deployment, Hypothesis, StrategyVersion
    dao = ResearchDAO()
    dao.insert_hypothesis(Hypothesis(
        id="H-20261026-001", version=1, prior="value", statement="x",
        universe="A", signal={}, horizon_days=5, benchmark="sh000300",
        metric="excess", threshold=">0", regimes=["all"], costs={},
        kill_criteria=["k"], status="locked"))
    dao.insert_strategy(StrategyVersion(
        id="S-BASE-v0", version=1, parent_id="", spec={}, status="draft"))
    dao.insert_deployment(Deployment(
        id="D-20261026-001", strategy_id="S-BASE-v0", mode="paper",
        start_date="2026-01-01", end_date="", capital=1_000_000,
        broker="paper", status="running"))


def test_record_close_and_evidence_parse(db):
    from src.research.decision import DecisionService, parse_evidence
    _seed()
    svc = DecisionService()
    decision = svc.record("promote", "S-BASE-v0", "进入纸盘", "实验通过",
                          {"E-20261026-001": "supported"}, "3 个月一致",
                          "2027-01-31")
    assert decision.id.startswith("DL-")
    assert parse_evidence("E-1=supported,E-2=rejected") == {
        "E-1": "supported", "E-2": "rejected"}
    with pytest.raises(ValueError):
        parse_evidence("E-1")
    closed = svc.close(decision.id, "已晋升")
    assert closed["status"] == "closed"
    assert closed["outcome"] == "已晋升"
    assert svc.dao.list_decisions("closed")[0]["id"] == decision.id


def test_decision_ids_are_unique(db):
    from src.research.decision import DecisionService
    _seed()
    svc = DecisionService()
    ids = {svc.record("promote", "S-BASE-v0", f"c{i}", "r").id for i in range(3)}
    assert len(ids) == 3


def test_require_rejects_without_decision(db):
    from src.research.decision import DecisionRequiredError, DecisionService
    _seed()
    svc = DecisionService()
    with pytest.raises(DecisionRequiredError):
        svc.require("strategy", "S-BASE-v0")
    with pytest.raises(DecisionRequiredError):
        svc.require("nope", "S-BASE-v0")


def test_transition_requires_decision_then_updates(db):
    from src.research.decision import DecisionRequiredError, DecisionService
    _seed()
    svc = DecisionService()
    with pytest.raises(DecisionRequiredError):
        svc.transition("strategy", "S-BASE-v0", "paper")
    svc.record("promote", "S-BASE-v0", "进入纸盘", "实验通过")
    result = svc.transition("strategy", "S-BASE-v0", "paper")
    assert result["status"] == "paper"
    assert svc.dao.get_strategy("S-BASE-v0")["status"] == "paper"


def test_transition_rejects_illegal_status_and_unknown_entity(db):
    from src.research.decision import DecisionRequiredError, DecisionService
    from src.research.domain import DomainError
    _seed()
    svc = DecisionService()
    svc.record("promote", "S-BASE-v0", "x", "y")
    with pytest.raises(DomainError):
        svc.transition("strategy", "S-BASE-v0", "live")
    with pytest.raises(DecisionRequiredError):
        svc.transition("strategy", "S-NOT-EXIST", "paper")


def test_hypothesis_transition_with_decision(db):
    from src.research.decision import DecisionService
    _seed()
    svc = DecisionService()
    svc.record("test", "H-20261026-001", "开始测试", "预注册完成")
    svc.transition("hypothesis", "H-20261026-001", "testing")
    assert svc.dao.get_hypothesis("H-20261026-001")["status"] == "testing"


def test_cli_create_and_list(db, capsys):
    from src.research.decision import main
    _seed()
    assert main(["create", "--type", "promote", "--target", "S-BASE-v0",
                 "--change", "进入纸盘", "--rationale", "实验通过",
                 "--evidence", "E-1=supported"]) == 0
    assert main(["list", "--status", "open"]) == 0
    out = capsys.readouterr().out
    assert "DL-" in out and "S-BASE-v0" in out
