"""数据质量门回归测试（R0-5）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "q.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _record(**over):
    d = {"dataset": "kline_daily", "trade_date": "2026-10-26",
         "coverage": 0.98, "missing_rate": 0.01, "outliers": 0,
         "pit_violations": 0, "details": {}}
    d.update(over)
    from src.research.dao import ResearchDAO
    ResearchDAO().insert_quality(d)


def test_pass_quality(db):
    from src.research.quality import assert_quality, evaluate
    _record()
    result = evaluate("kline_daily", "2026-10-26")
    assert result.status == "pass"
    assert result.ok
    assert assert_quality("kline_daily", "2026-10-26").status == "pass"


def test_missing_record_is_invalid(db):
    from src.research.quality import evaluate
    result = evaluate("kline_daily", "2026-10-26")
    assert result.status == "invalid"
    assert "缺少质量记录" in result.reasons[0]


def test_low_coverage_is_invalid(db):
    from src.research.quality import QualityError, assert_quality
    _record(coverage=0.80)
    with pytest.raises(QualityError):
        assert_quality("kline_daily", "2026-10-26")


def test_pit_violation_is_invalid(db):
    from src.research.quality import QualityError, assert_quality
    _record(pit_violations=2)
    with pytest.raises(QualityError):
        assert_quality("kline_daily", "2026-10-26")


def test_missing_rate_warn_but_ok(db):
    from src.research.quality import evaluate
    _record(missing_rate=0.20)
    result = evaluate("kline_daily", "2026-10-26")
    assert result.status == "warn"
    assert result.ok


def test_stale_is_warn(db):
    from src.research.quality import evaluate
    _record(trade_date="2026-10-01")
    result = evaluate("kline_daily", "2026-10-01")
    assert result.status == "warn"
    assert any("陈旧" in r for r in result.reasons)


def test_record_quality_returns_verdict(db):
    from src.research.quality import record_quality
    result = record_quality("kline_daily", "2026-10-26", 0.99, 0.0, 0, 0)
    assert result.status == "pass"
