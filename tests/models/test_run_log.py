"""RunLogDAO.get_latest_completed_run_id：排除周六复盘 run（DAO 改动附单测）。"""

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    return db_mod


def _add(db_mod, run_id, start, status="completed"):
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT INTO run_log (run_id, start_time, status) VALUES (?,?,?)",
            (run_id, start, status))


class TestLatestCompleted:
    def test_skips_review_run(self, db):
        _add(db, "20260925_153016", "2026-09-25T15:30:16")
        _add(db, "20260926_000027_review", "2026-09-26T00:00:27")
        assert db.RunLogDAO().get_latest_completed_run_id() == "20260925_153016"

    def test_none_when_empty(self, db):
        assert db.RunLogDAO().get_latest_completed_run_id() is None

    def test_failed_excluded(self, db):
        _add(db, "20260925_153016", "2026-09-25T15:30:16", status="failed")
        assert db.RunLogDAO().get_latest_completed_run_id() is None


class TestLatestRun:
    """首页"上次运行/候选数"必须取最新**筛选**轮，不能被周六复盘 run 顶掉。"""

    def test_skips_review_run(self, db):
        _add(db, "20260925_153016", "2026-09-25T15:30:16")
        _add(db, "20260926_000027_review", "2026-09-26T00:00:27")
        run = db.RunLogDAO().get_latest_run()
        assert run["run_id"] == "20260925_153016"

    def test_returns_running_screening_run(self, db):
        _add(db, "20260925_153016", "2026-09-25T15:30:16", status="running")
        assert db.RunLogDAO().get_latest_run()["status"] == "running"
