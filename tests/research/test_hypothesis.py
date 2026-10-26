"""假设卡回归测试（R2-1）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "h.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _create(service, **over):
    data = dict(statement="MOS 有预测力", prior="value", universe="A",
                signal={"feature": "mos"}, horizon_days=126,
                benchmark="sh000300", metric="excess", threshold=">0",
                regimes=["all"], costs={}, kill_criteria=["超额连续两年为负"])
    data.update(over)
    return service.create(**data)


def test_create_lock_view_list(db):
    from src.research.hypothesis import HypothesisService
    service = HypothesisService()
    row = _create(service)
    assert row["status"] == "draft"
    assert row["id"].startswith("H-")
    locked = service.lock(row["id"])
    assert locked["status"] == "locked"
    assert locked["locked_at"]
    assert service.list("locked")[0]["id"] == row["id"]


def test_locked_cannot_be_updated(db):
    from src.research.hypothesis import (
        HypothesisLockedError, HypothesisService,
    )
    service = HypothesisService()
    row = _create(service)
    service.lock(row["id"])
    with pytest.raises(HypothesisLockedError):
        service.update(row["id"], statement="改")


def test_draft_can_be_updated(db):
    from src.research.hypothesis import HypothesisService
    service = HypothesisService()
    row = _create(service)
    updated = service.update(row["id"], statement="改后的陈述")
    assert updated["statement"] == "改后的陈述"
    assert updated["status"] == "draft"


def test_lock_rejects_illegal_transition(db):
    from src.research.domain import DomainError
    from src.research.hypothesis import HypothesisService
    service = HypothesisService()
    row = _create(service)
    service.lock(row["id"])
    with pytest.raises(DomainError):
        service.lock(row["id"])


def test_transition_to_testing(db):
    from src.research.hypothesis import HypothesisService
    service = HypothesisService()
    row = _create(service)
    service.lock(row["id"])
    assert service.transition(row["id"], "testing")["status"] == "testing"


def test_cli_create_lock_view(db, capsys):
    from src.research.hypothesis import main
    assert main(["create", "--statement", "MOS 有预测力", "--signal",
                 '{"feature":"mos"}', "--kill-criteria", "超额为负"]) == 0
    hid = capsys.readouterr().out.strip()
    assert hid.startswith("H-")
    assert main(["lock", hid]) == 0
    assert main(["view", hid]) == 0
    assert "locked" in capsys.readouterr().out


def test_view_missing_raises(db):
    from src.research.hypothesis import HypothesisError, HypothesisService
    with pytest.raises(HypothesisError):
        HypothesisService().view("H-NOPE")
