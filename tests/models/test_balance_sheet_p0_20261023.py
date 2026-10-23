"""P0-3 资产负债表采集回归测试（2026-10-23）。

- 有息负债 = 短借 + 一年内到期非流动负债 + 长借 + 应付债券 + 短期应付债券
- fetch_balance_sheet_annual：直连 F10，company_type 缺省 4 且空则回退 1/2/3
- FinancialHistoryDAO 持久化新列
"""

import urllib.parse

import pytest

from src.collector import balance_sheet_fetcher as bsf


def test_interest_bearing_debt_sum():
    row = {
        "SHORT_LOAN": 100, "LONG_LOAN": 200, "BOND_PAYABLE": 50,
        "NONCURRENT_LIAB_1YEAR": 20, "SHORT_BOND_PAYABLE": 5,
    }
    assert bsf._interest_bearing_debt(row) == 375
    assert bsf._interest_bearing_debt({"SHORT_LOAN": 10}) == 10
    assert bsf._interest_bearing_debt({}) is None


def test_em_symbol():
    assert bsf._em_symbol("600519") == "SH600519"
    assert bsf._em_symbol("000001") == "SZ000001"


def test_fetch_balance_sheet_annual_parses(monkeypatch):
    def fake(url, timeout=15):
        if "zcfzbDateAjaxNew" in url:
            return {"data": [
                {"REPORT_DATE": "2025-12-31 00:00:00"},
                {"REPORT_DATE": "2024-12-31 00:00:00"},
                {"REPORT_DATE": "2025-06-30 00:00:00"},  # 非年报应忽略
            ]}
        # zcfzbAjaxNew：按 dates 参数返回每个日期一行
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        dates = (qs.get("dates", [""])[0] or "").split(",")
        return {"data": [
            {"REPORT_DATE": f"{d} 00:00:00", "MONETARYFUNDS": 1000,
             "SHORT_LOAN": 10, "LONG_LOAN": 20, "GOODWILL": 5,
             "TOTAL_ASSETS": 5000, "TOTAL_EQUITY": 3000}
            for d in dates if d
        ]}

    monkeypatch.setattr(bsf, "_get_json", fake)
    out = bsf.fetch_balance_sheet_annual("600519")
    assert set(out) == {"2025-12-31", "2024-12-31"}
    assert out["2025-12-31"]["cash"] == 1000
    assert out["2025-12-31"]["interest_bearing_debt"] == 30
    assert out["2025-12-31"]["goodwill"] == 5
    assert out["2025-12-31"]["total_assets"] == 5000


def test_fetch_balance_sheet_annual_empty_when_no_dates(monkeypatch):
    monkeypatch.setattr(bsf, "_get_json", lambda url, timeout=15: {"data": []})
    assert bsf.fetch_balance_sheet_annual("000001") == {}


def test_dao_persists_new_columns(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "b.db"))
    db_mod.init_database()
    from src.models.database import FinancialHistoryDAO
    FinancialHistoryDAO().batch_save([{
        "stock_code": "600519", "report_date": "2025-12-31",
        "roe": 30.0, "cash": 59295822956.89, "interest_bearing_debt": None,
        "goodwill": None, "total_assets": 303834844021.44,
        "total_equity": 253959253909.07,
    }])
    with db_mod.db_conn() as conn:
        row = conn.execute(
            "SELECT cash, total_assets, total_equity FROM financial_history "
            "WHERE stock_code='600519'").fetchone()
    assert row["cash"] == 59295822956.89
    assert row["total_assets"] == 303834844021.44
    assert row["total_equity"] == 253959253909.07
