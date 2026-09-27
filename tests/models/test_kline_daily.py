"""KlineDAO 单测（K 线 500 根因：类被历史重构误删，routes/scheduler 引用悬空）。"""

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    return db_mod


def _row(date, close):
    return {"trade_date": date, "open": close - 1, "close": close,
            "high": close + 1, "low": close - 2, "volume": 100.0,
            "amount": 1000.0, "turnover": 1.0}


class TestKlineDAO:
    def test_empty(self, db):
        dao = db.KlineDAO()
        assert dao.get_daily("000792") == []
        assert dao.get_latest_date("000792") is None
        assert dao.upsert_many("000792", []) == 0

    def test_upsert_get_order_limit(self, db):
        dao = db.KlineDAO()
        assert dao.upsert_many("000792", [
            _row("2026-09-25", 10.0),
            _row("2026-09-23", 9.0),
            _row("2026-09-24", 9.5),
        ]) == 3
        rows = dao.get_daily("000792")
        assert [r["trade_date"] for r in rows] == [
            "2026-09-23", "2026-09-24", "2026-09-25"]
        assert [r["code"] for r in rows] == ["000792"] * 3
        assert len(dao.get_daily("000792", limit=2)) == 2
        assert dao.get_daily("000792", limit=2)[0]["trade_date"] == "2026-09-24"
        assert dao.get_latest_date("000792") == "2026-09-25"

    def test_upsert_replace(self, db):
        dao = db.KlineDAO()
        dao.upsert_many("000792", [_row("2026-09-25", 10.0)])
        dao.upsert_many("000792", [_row("2026-09-25", 11.0)])
        rows = dao.get_daily("000792")
        assert len(rows) == 1
        assert rows[0]["close"] == 11.0
