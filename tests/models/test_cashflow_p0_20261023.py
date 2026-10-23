"""P0-4 现金流 / P0-10 披露日期回归测试（2026-10-23）。

- F10 现金流量表：capex = CONSTRUCT_LONG_ASSET；net_operate_cashflow = NETCASH_OPERATE
- 东财主财务兜底返回 notice_date（披露日期）
- FinancialHistoryDAO 持久化 capex / net_operate_cashflow / notice_date
"""

import json
import urllib.parse

from src.collector import cashflow_fetcher as cff


def test_fetch_cash_flow_parses(monkeypatch):
    def fake(url, timeout=15):
        if "xjllbDateAjaxNew" in url:
            return {"data": [
                {"REPORT_DATE": "2025-12-31 00:00:00"},
                {"REPORT_DATE": "2024-12-31 00:00:00"},
                {"REPORT_DATE": "2025-06-30 00:00:00"},  # 非年报忽略
            ]}
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        dates = (qs.get("dates", [""])[0] or "").split(",")
        return {"data": [
            {"REPORT_DATE": f"{d} 00:00:00", "CONSTRUCT_LONG_ASSET": 1000,
             "NETCASH_OPERATE": 5000}
            for d in dates if d
        ]}

    monkeypatch.setattr(cff, "_get_json", fake)
    out = cff.fetch_cash_flow_annual("600519")
    assert set(out) == {"2025-12-31", "2024-12-31"}
    assert out["2025-12-31"]["capex"] == 1000
    assert out["2025-12-31"]["net_operate_cashflow"] == 5000


def test_fetch_cash_flow_empty(monkeypatch):
    monkeypatch.setattr(cff, "_get_json", lambda url, timeout=15: {"data": []})
    assert cff.fetch_cash_flow_annual("000001") == {}


def test_roic_fcf_includes_notice_date(monkeypatch):
    from src.collector import akshare_fetcher as akf
    raw = json.dumps({"result": {"data": [{
        "REPORT_TYPE": "年报", "REPORT_DATE": "2025-12-31 00:00:00",
        "NOTICE_DATE": "2026-04-17 00:00:00", "ROIC": 31.4,
        "FCFF_BACK": 76139290546.69, "TOTAL_SHARE": 1256197800.0,
    }]}})
    monkeypatch.setattr(akf, "curl_get", lambda url, timeout=10: raw)
    out = akf._fetch_eastmoney_roic_fcf("600519")
    assert out["2025-12-31"]["notice_date"] == "2026-04-17"
    assert out["2025-12-31"]["roic"] == 31.4
    assert out["2025-12-31"]["fcf"] == 76139290546.69
    assert out["2025-12-31"]["total_share"] == 1256197800.0


def test_dao_persists_cashflow_and_notice(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "cf.db"))
    db_mod.init_database()
    from src.models.database import FinancialHistoryDAO
    FinancialHistoryDAO().batch_save([{
        "stock_code": "600519", "report_date": "2025-12-31",
        "capex": 4678712053.56, "net_operate_cashflow": 92463692168.43,
        "notice_date": "2026-04-17",
    }])
    with db_mod.db_conn() as conn:
        row = conn.execute(
            "SELECT capex, net_operate_cashflow, notice_date FROM financial_history "
            "WHERE stock_code='600519'").fetchone()
    assert row["capex"] == 4678712053.56
    assert row["net_operate_cashflow"] == 92463692168.43
    assert row["notice_date"] == "2026-04-17"
