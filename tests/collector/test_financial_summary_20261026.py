"""财务汇总语义回归（2026-10-26，审计 D1）。

- 5y/10y ROE 任一侧缺失时，roe_improvement 必须为 None，不能拿 0 相减
- 0.0 是有效值（0% ROE），不能因 truthiness 被写成 NULL
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    return db_mod


def _seed(code, roes):
    rows = []
    for i, roe in enumerate(roes):
        rows.append({"stock_code": code,
                     "report_date": f"{2016 + i}-12-31", "roe": roe})
    db_mod.FinancialHistoryDAO().batch_save(rows)


def test_missing_5y_roe_does_not_fake_trend(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    # 2016-2020 有 ROE，2021-2025 缺失 → 5y 缺失、10y=10，趋势必须是 None
    _seed("600519", [10.0] * 5 + [None] * 5)
    rebuild_financial_summaries([{"code": "600519"}])
    s = db_mod.FinancialSummaryDAO().get("600519")
    assert s["roe_5y_avg"] is None
    assert s["roe_10y_avg"] == 10.0
    assert s["roe_improvement"] is None


def test_zero_roe_is_preserved_not_null(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed("000001", [10.0] * 5 + [0.0] * 5)
    rebuild_financial_summaries([{"code": "000001"}])
    s = db_mod.FinancialSummaryDAO().get("000001")
    assert s["roe_5y_avg"] == 0.0
    assert s["roe_10y_avg"] == 5.0
    assert s["roe_improvement"] == -5.0
