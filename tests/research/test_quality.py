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


def test_assess_analysis_outcome_records_quality(db):
    from src.models.database import db_conn

    from src.research.quality import (
        assess_analysis_outcome, assess_dataset, evaluate,
    )
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO analysis_outcome (analysis_id, benchmark_code, stock_code, "
            "analysis_date, entry_date, ret_20, excess_20, updated_at) "
            "VALUES (1, 'sh000300', '600519', '2026-07-08', '2026-07-09', "
            "0.01, 0.005, '2026-10-01')")
    stats = assess_analysis_outcome("2026-09-30")
    assert stats["coverage"] == 1.0
    assert stats["outliers"] == 0
    assert stats["pit_violations"] == 0
    assert assess_dataset("analysis_outcome", "2026-09-30") == stats
    assert assess_dataset("unknown_dataset", "2026-09-30") is None


def test_assess_detects_outlier(db):
    from src.models.database import db_conn

    from src.research.quality import assess_analysis_outcome
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO analysis_outcome (analysis_id, benchmark_code, stock_code, "
            "analysis_date, entry_date, ret_20, excess_20, updated_at) "
            "VALUES (1, 'sh000300', '600519', '2026-07-08', '2026-07-09', "
            "2.0, 1.5, '2026-10-01')")
    stats = assess_analysis_outcome("2026-09-30")
    assert stats["outliers"] == 1


def test_run_experiment_auto_assesses_outcome_quality(db):
    """D-QG 修复：analysis_outcome 数据集在实验运行时自动评估并落质量记录。"""
    from src.research.quality import assess_dataset
    assert assess_dataset("analysis_outcome", "2026-09-30") is not None


def test_old_date_is_looked_up_exactly_not_by_recent_window(db):
    """回归 D2：早于最近 200 条的日期不得被误判为缺记录。"""
    from src.research.dao import ResearchDAO
    from src.research.quality import evaluate
    dao = ResearchDAO()
    for i in range(250):
        dao.insert_quality({
            "dataset": "kline_daily", "trade_date": f"2026-01-{i:02d}",
            "coverage": 1.0, "missing_rate": 0.0, "outliers": 0,
            "pit_violations": 0, "status": "recorded", "details": {}})
    result = evaluate("kline_daily", "2026-01-05")
    # 不得因"漏判"为 invalid（可能因陈旧为 warn）
    assert "缺少质量记录" not in result.reasons[0]
    assert result.status != "invalid"
    assert dao.get_quality("kline_daily", "2026-01-05") is not None
    assert dao.get_quality("kline_daily", "1999-01-01") is None


def test_record_quality_returns_verdict(db):
    from src.research.quality import record_quality
    result = record_quality("kline_daily", "2026-10-26", 0.99, 0.0, 0, 0)
    assert result.status == "pass"


def test_assess_kline_daily(db):
    from src.research.quality import assess_dataset
    dao = db_mod.KlineDAO()
    dao.upsert_many("600519", [{"trade_date": "2026-10-26", "open": 1.0,
                                "close": 1.0, "high": 1.0, "low": 1.0,
                                "volume": 1.0, "amount": 1.0, "turnover": 0.1,
                                "close_raw": 1.0, "close_adj": 1.0}])
    dao.upsert_many("000001", [{"trade_date": "2026-10-26", "open": 1.0,
                                "close": 1.0, "high": 1.0, "low": 1.0,
                                "volume": 1.0, "amount": 1.0, "turnover": 0.1}])
    stats = assess_dataset("kline_daily", "2026-10-26")
    assert stats["coverage"] == 0.5
    assert stats["details"]["codes"] == 2
