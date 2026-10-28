"""财务汇总语义回归（2026-10-26，审计 D1；2026-10-28 追加 N1）。

- 5y/10y ROE 任一侧缺失时，roe_improvement 必须为 None，不能拿 0 相减
- 0.0 是有效值（0% ROE），不能因 truthiness 被写成 NULL
- 股本稀释率方向必须正确：扩股为正、回购为负（门规 9 依赖此符号）
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


# ── 股本稀释率方向（2026-10-28，审计 N1）────────────────────────────────
# reports 按 report_date 升序，故 5y 窗口 = 最近 5 份年报，[-1] 是最新。
# 旧实现把 earliest/latest 取反：扩股算出负数（门规 9 放行）、回购算出正数（被误杀）。

def _seed_shares(code, shares_by_year):
    rows = [{"stock_code": code, "report_date": f"{year}-12-31",
              "total_shares": float(shares)}
            for year, shares in enumerate(shares_by_year, start=2016)]
    db_mod.FinancialHistoryDAO().batch_save(rows)


def test_share_issuance_is_positive_dilution(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("600519", [100] * 9 + [200])   # 2025 年翻倍扩股
    rebuild_financial_summaries([{"code": "600519"}])
    s = db_mod.FinancialSummaryDAO().get("600519")
    assert s["share_dilution_5y"] == 100.0
    assert s["share_dilution_10y"] == 100.0


def test_buyback_is_negative_dilution(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("000858", [200] * 9 + [100])   # 2025 年回购注销一半
    rebuild_financial_summaries([{"code": "000858"}])
    s = db_mod.FinancialSummaryDAO().get("000858")
    assert s["share_dilution_5y"] == -50.0
    assert s["share_dilution_10y"] == -50.0


def test_dilution_5y_window_is_recent_years_only(db):
    """扩股只发生在 2017：5y 窗口应为 0（不含早年），10y 窗口应看到 +100。"""
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("600036", [100, 200] + [200] * 8)
    rebuild_financial_summaries([{"code": "600036"}])
    s = db_mod.FinancialSummaryDAO().get("600036")
    assert s["share_dilution_5y"] == 0.0
    assert s["share_dilution_10y"] == 100.0
