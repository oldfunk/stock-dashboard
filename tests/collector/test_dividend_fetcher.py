"""股息采集与挂载单测（2026-10-21）。

hermetic：不联网；用 tmp 库 + 原始行 dict。
重点：每 10 股口径换算、缺数据保持 None、红利策略的数据底座不造假。
"""

from datetime import datetime

import pytest

from src.collector.dividend_fetcher import (
    attach_dividends,
    latest_annual_report_date,
    normalize_fhps_rows,
)


class TestLatestAnnualReport:
    def test_before_may_uses_two_years_back(self):
        assert latest_annual_report_date(datetime(2026, 4, 30)) == "2024-12-31"

    def test_may_uses_previous_year(self):
        assert latest_annual_report_date(datetime(2026, 5, 1)) == "2025-12-31"
        assert latest_annual_report_date(datetime(2026, 10, 21)) == "2025-12-31"


class TestNormalize:
    def _row(self, **kw):
        base = {
            "代码": "000001",
            "现金分红-现金分红比例": 2.0,   # 每 10 股 2 元
            "每股收益": 1.0,
            "现金分红-股息率": 0.02,        # 小数口径
        }
        base.update(kw)
        return base

    def test_per_ten_shares_and_percent_conversion(self):
        recs = normalize_fhps_rows([self._row()], "2025-12-31")
        assert len(recs) == 1
        r = recs[0]
        assert r["stock_code"] == "000001"
        assert r["cash_per_share"] == 0.2
        assert r["eps"] == 1.0
        assert r["reported_yield"] == 2.0

    def test_zero_or_missing_cash_is_skipped(self):
        rows = [
            self._row(**{"代码": "000002", "现金分红-现金分红比例": 0}),
            self._row(**{"代码": "000003", "现金分红-现金分红比例": None}),
            self._row(**{"代码": "000004", "现金分红-现金分红比例": float("nan")}),
        ]
        assert normalize_fhps_rows(rows, "2025-12-31") == []

    def test_duplicate_code_keeps_higher_cash(self):
        rows = [
            self._row(**{"代码": "000005", "现金分红-现金分红比例": 1.0}),
            self._row(**{"代码": "000005", "现金分红-现金分红比例": 3.0}),
        ]
        recs = normalize_fhps_rows(rows, "2025-12-31")
        assert len(recs) == 1
        assert recs[0]["cash_per_share"] == 0.3

    def test_negative_eps_kept_but_payout_not_computed_here(self):
        recs = normalize_fhps_rows(
            [self._row(**{"每股收益": -0.5})], "2025-12-31")
        assert recs[0]["eps"] == -0.5


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "d.db"))
    db_mod.init_database()
    return db_mod


class TestAttachDividends:
    def test_computes_yield_and_payout(self, db):
        db.DividendDAO().upsert_many([{
            "stock_code": "000001", "report_date": "2025-12-31",
            "cash_per_share": 0.2, "eps": 1.0, "reported_yield": 2.0,
        }])
        stocks = [
            {"code": "000001", "name": "平安银行", "price": 10.0},
            {"code": "000002", "name": "无数据", "price": 5.0},
        ]
        attach_dividends(stocks, report_date="2025-12-31")
        assert stocks[0]["dividend_yield"] == 2.0
        assert stocks[0]["payout_ratio"] == 20.0
        assert stocks[0]["dividend_year"] == "2025"
        # 缺数据不填 0，也不写伪造字段
        assert "dividend_yield" not in stocks[1]
        assert "payout_ratio" not in stocks[1]

    def test_negative_eps_does_not_produce_payout(self, db):
        db.DividendDAO().upsert_many([{
            "stock_code": "000003", "report_date": "2025-12-31",
            "cash_per_share": 0.1, "eps": -1.0, "reported_yield": 1.0,
        }])
        stocks = [{"code": "000003", "price": 10.0}]
        attach_dividends(stocks, report_date="2025-12-31")
        assert stocks[0]["dividend_yield"] == 1.0
        assert "payout_ratio" not in stocks[0]

    def test_missing_price_leaves_yield_unset(self, db):
        db.DividendDAO().upsert_many([{
            "stock_code": "000004", "report_date": "2025-12-31",
            "cash_per_share": 0.2, "eps": 1.0, "reported_yield": 2.0,
        }])
        stocks = [{"code": "000004"}]
        attach_dividends(stocks, report_date="2025-12-31")
        assert "dividend_yield" not in stocks[0]
        assert stocks[0]["payout_ratio"] == 20.0
