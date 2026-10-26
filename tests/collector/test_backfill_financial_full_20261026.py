"""全市场年报回填回归测试（2026-10-26，P5-2）。

- build_records：三源合并、只保留年报、total_share→total_shares
- target_codes：默认排除已有年报的股票
- pending_codes：进度表断点续跑
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "f.db"))
    db_mod.init_database()
    return db_mod


def _snap(code):
    db_mod.StockSnapshotDAO().save_batch([{
        "code": code, "name": "测试" + code, "market": "A", "sector": "测试",
        "pe": 10.0, "pb": 1.0, "ps": 1.0, "market_cap": 100.0,
        "circulating_cap": 90.0, "roe": 10.0, "revenue": 10.0,
        "revenue_growth": 5.0, "profit": 2.0, "profit_growth": 5.0,
        "debt_ratio": 30.0, "current_price": 10.0, "high_52w": 12.0,
        "low_52w": 8.0, "is_st": False, "list_date": "2010-01-01",
        "snapshot_date": "2026-10-23",
    }])


def _annual(code):
    db_mod.FinancialHistoryDAO().batch_save([{
        "stock_code": code, "report_date": "2025-12-31", "roe": 10.0,
    }])


def test_build_records_merges_sources_and_drops_quarterly():
    from scripts.backfill_financial_full import build_records
    mf = {"2025-12-31": {"roe": 10.0, "eps": 1.0, "total_share": 100.0,
                         "notice_date": "2026-04-01"}}
    bs = {"2025-12-31": {"cash": 5.0, "interest_bearing_debt": 2.0}}
    cf = {"2025-12-31": {"capex": 1.0, "net_operate_cashflow": 3.0},
          "2025-06-30": {"capex": 9.0}}
    records = build_records("600519", mf, bs, cf)
    assert len(records) == 1
    rec = records[0]
    assert rec["report_date"] == "2025-12-31"
    assert rec["total_shares"] == 100.0
    assert rec["roe"] == 10.0
    assert rec["cash"] == 5.0
    assert rec["capex"] == 1.0
    assert rec["net_operate_cashflow"] == 3.0


def test_build_records_empty():
    from scripts.backfill_financial_full import build_records
    assert build_records("600519", {}, {}, {}) == []


def test_target_codes_excludes_existing_annual(db):
    from scripts.backfill_financial_full import target_codes
    _snap("600519")
    _snap("000001")
    _annual("600519")
    assert target_codes() == ["000001"]
    assert target_codes(all_codes=True) == ["000001", "600519"]


def test_pending_codes_resumes(db):
    from scripts.backfill_financial_full import pending_codes
    dao = db_mod.FinancialHistoryDAO()
    dao.mark_backfill_done("financial_full", "600519", 1, "done")
    dao.mark_backfill_done("financial_full", "000001", 0, "no_data")
    assert pending_codes(["600519", "000001", "000002"]) == ["000002"]
    assert pending_codes(["600519", "000001"], retry_no_data=True) == ["000001"]


def test_main_dry_run(db, monkeypatch, capsys):
    from scripts import backfill_financial_full as s
    _snap("600519")
    _snap("000001")
    monkeypatch.setattr("sys.argv", ["backfill", "--dry-run"])
    assert s.main() == 0
    out = capsys.readouterr().out
    assert '"target_codes": 2' in out
