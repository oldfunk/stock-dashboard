"""全市场年报补充字段回填回归测试（2026-10-24）。

- DAO `update_extra_fields`：只填 NULL、列白名单、只作用于年报行
- DAO `get_codes_missing_extra`：有 NULL 即入选，回填后自然消失（断点续跑）
- `collect_extra_for_code`：三源合并、total_share→total_shares、单源失败不影响其他源
- `fetch_mainfin_annual`：只取年报，解析 notice_date/fcf/roic/total_share
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "f.db"))
    db_mod.init_database()
    return db_mod


def _save_annual(code="600519", report_date="2025-12-31", **extra):
    from src.models.database import FinancialHistoryDAO
    rec = {"stock_code": code, "report_date": report_date, "roe": 20.0, "eps": 5.0}
    rec.update(extra)
    FinancialHistoryDAO().batch_save([rec])


def test_update_extra_fields_only_fills_nulls(db):
    from src.models.database import FinancialHistoryDAO
    _save_annual(cash=1.0)  # cash 已有值
    dao = FinancialHistoryDAO()
    n = dao.update_extra_fields("600519", "2025-12-31",
                                {"cash": 999.0, "capex": 7.0, "notice_date": "2026-04-01"})
    assert n == 1
    row = dao.get_annual_reports("600519")[0]
    assert row["cash"] == 1.0            # 已有值不被覆盖（幂等）
    assert row["capex"] == 7.0
    assert row["notice_date"] == "2026-04-01"
    # 重跑：全部已有值 → 无变化
    dao.update_extra_fields("600519", "2025-12-31", {"capex": 8.0})
    assert dao.get_annual_reports("600519")[0]["capex"] == 7.0


def test_update_extra_fields_whitelist_and_report_type(db):
    from src.models.database import FinancialHistoryDAO, db_conn
    _save_annual(report_date="2025-03-31")  # 季报行（report_type='Q'）
    dao = FinancialHistoryDAO()
    # 白名单外的列被忽略
    assert dao.update_extra_fields("600519", "2025-03-31", {"roe": 1.0}) == 0
    # 只作用于年报行
    assert dao.update_extra_fields("600519", "2025-03-31", {"cash": 5.0}) == 0
    with db_conn() as conn:
        row = conn.execute(
            "SELECT cash, roe FROM financial_history WHERE report_date='2025-03-31'"
        ).fetchone()
    assert row["cash"] is None and row["roe"] == 20.0


def test_get_codes_missing_extra_shrinks_after_backfill(db):
    from src.models.database import FinancialHistoryDAO
    _save_annual(code="600519")
    _save_annual(code="000001")
    _save_annual(code="000002", cash=1.0, capex=1.0, notice_date="2026-04-01")
    dao = FinancialHistoryDAO()
    missing = dao.get_codes_missing_extra()
    assert missing == ["000001", "600519"]        # 000002 已完整
    dao.update_extra_fields("600519", "2025-12-31",
                            {"cash": 1.0, "capex": 1.0, "notice_date": "2026-04-01"})
    assert dao.get_codes_missing_extra() == ["000001"]


def test_collect_extra_for_code_merges_sources():
    from scripts.backfill_financial_extra import collect_extra_for_code

    def bs(code):
        return {"2025-12-31": {"cash": 10.0, "total_equity": 30.0}}

    def cf(code):
        return {"2025-12-31": {"capex": 4.0, "net_operate_cashflow": 9.0}}

    def mf(code):
        return {"2025-12-31": {"notice_date": "2026-04-01", "total_share": 5.0,
                               "fcf": 2.0}}

    payload = collect_extra_for_code("600519", bs, cf, mf)
    assert payload["2025-12-31"] == {
        "cash": 10.0, "total_equity": 30.0, "capex": 4.0,
        "net_operate_cashflow": 9.0, "notice_date": "2026-04-01",
        "total_shares": 5.0, "fcf": 2.0,
    }


def test_collect_extra_survives_source_failure():
    from scripts.backfill_financial_extra import collect_extra_for_code

    def boom(code):
        raise RuntimeError("network down")

    payload = collect_extra_for_code(
        "600519", boom, lambda c: {"2025-12-31": {"capex": 1.0}},
        lambda c: {"2025-12-31": {"notice_date": "2026-04-01"}})
    assert payload["2025-12-31"] == {"capex": 1.0, "notice_date": "2026-04-01"}


def test_apply_code_skips_unknown_report_dates(db):
    from src.models.database import FinancialHistoryDAO
    from scripts.backfill_financial_extra import apply_code
    _save_annual(code="600519", report_date="2025-12-31")
    dao = FinancialHistoryDAO()
    # 库里没有 2019-12-31 这行 → 不新增行（绝不因回填造出半截行）
    n = apply_code(dao, "600519", {"2019-12-31": {"cash": 1.0}})
    assert n == 0
    assert len(dao.get_annual_reports("600519")) == 1


def test_fetch_mainfin_annual_parses_annual_only(monkeypatch):
    from src.collector import mainfin_fetcher as mf

    def fake_get_json(url, timeout=15, headers=None):
        assert "SECUCODE%3D%22600519.SH%22" in url
        return {"result": {"data": [
            {"REPORT_TYPE": "年报", "REPORT_DATE": "2025-12-31 00:00:00",
             "NOTICE_DATE": "2026-04-01 00:00:00", "FCFF_BACK": 100.0,
             "ROIC": 0.18, "TOTAL_SHARE": 12.5},
            {"REPORT_TYPE": "三季报", "REPORT_DATE": "2025-09-30 00:00:00",
             "NOTICE_DATE": "2025-10-25 00:00:00", "FCFF_BACK": 50.0,
             "ROIC": 0.1, "TOTAL_SHARE": 12.5},
        ]}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    out = mf.fetch_mainfin_annual("600519")
    assert list(out.keys()) == ["2025-12-31"]      # 三季报被剔除
    assert out["2025-12-31"]["notice_date"] == "2026-04-01"
    assert out["2025-12-31"]["fcf"] == 100.0
    assert out["2025-12-31"]["total_share"] == 12.5
