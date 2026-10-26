"""Universe/退市名单回归测试（R0-13）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "u.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _sh_rows():
    return [
        {"公司代码": "600001", "公司简称": "退市甲", "退市日期": "2020-01-01"},
        {"code": "600002", "name": "退市乙", "delist_date": "2021-05-06"},
    ]


def _sz_rows():
    return [{"公司代码": "000001", "公司简称": "退市丙", "退市日期": "2019-03-04"}]


def test_normalize_delisted_row():
    from src.research.universe import UniverseError, normalize_delisted_row
    row = normalize_delisted_row(
        {"公司代码": "600001", "公司简称": "X", "退市日期": "2020-01-01"}, "CN")
    assert row["instrument_id"] == "CN:600001"
    assert row["symbol"] == "600001"
    assert row["delist_date"] == "2020-01-01"
    assert row["status"] == "delisted"
    assert normalize_delisted_row({"code": "1"}, "CN")["symbol"] == "000001"
    with pytest.raises(UniverseError):
        normalize_delisted_row({"公司简称": "X"}, "CN")


def test_import_delisted_writes_instrument_and_membership(db):
    from src.research.dao import ResearchDAO
    from src.research.universe import DELISTED_UNIVERSE, import_delisted
    result = import_delisted(_sh_rows(), _sz_rows())
    assert result["sh"] == 2 and result["sz"] == 1 and result["written"] == 3
    assert ResearchDAO().count_instruments("delisted") == 3
    with db_mod.db_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM universe_membership WHERE universe_id = ?",
            (DELISTED_UNIVERSE,)).fetchone()[0]
    assert n == 3


def test_import_delisted_dry_run(db):
    from src.research.dao import ResearchDAO
    from src.research.universe import import_delisted
    result = import_delisted(_sh_rows(), _sz_rows(), dry_run=True)
    assert result["would_write"] == 3
    assert ResearchDAO().count_instruments("delisted") == 0


def test_record_daily_marks_current_only(db):
    from src.research.universe import (
        ALL_UNIVERSE, COVERAGE_CURRENT_ONLY, import_delisted, record_daily,
    )
    import_delisted(_sh_rows(), _sz_rows())
    result = record_daily(trade_date="2026-10-26")
    assert result["recorded"] == 3
    assert result["coverage"] == COVERAGE_CURRENT_ONLY
    with db_mod.db_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM universe_membership WHERE universe_id = ?",
            (ALL_UNIVERSE,)).fetchone()[0]
    assert n == 3


def test_delisted_instruments_queryable(db):
    from src.research.universe import delisted_instruments, import_delisted
    import_delisted(_sh_rows(), _sz_rows())
    rows = delisted_instruments()
    assert len(rows) == 3
    assert all(r["status"] == "delisted" for r in rows)
