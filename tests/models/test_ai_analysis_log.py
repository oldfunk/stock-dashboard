"""AiAnalysisLogDAO.get_by_run 单测（用量展示用读方法）。"""

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    return db_mod


class TestGetByRun:
    def test_roundtrip_order_and_empty(self, db):
        dao = db.AiAnalysisLogDAO()
        assert dao.get_by_run("nope") == []
        dao.log("r1", "600519", "m-a", 100, 50, 0.0)
        dao.log("r1", "000858", "m-a", 200, 60, 0.0)
        dao.log("r2", "600519", "m-b", 10, 5, 0.0)
        rows = dao.get_by_run("r1")
        assert [r["code"] for r in rows] == ["600519", "000858"]
        assert rows[0]["prompt_tokens"] == 100
        assert rows[1]["completion_tokens"] == 60
        assert {r["model"] for r in rows} == {"m-a"}
