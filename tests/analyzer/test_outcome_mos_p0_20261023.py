"""P0-9 买入安全边际（MOS）记录与预测力验证回归测试（2026-10-23）。

- _mos_at_entry：notice_date <= 分析日 的最新年报 + 入场价算模型 MOS
- summarize_by_mos：按 MOS 分层统计收益/超额/胜率
"""

import pytest

from src.models import database as db_mod
from src.outcome_service import _mos_at_entry, summarize_by_mos


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "m.db"))
    db_mod.init_database()
    return db_mod


def test_mos_at_entry_uses_notice_date(db):
    from src.models.database import FinancialHistoryDAO
    FinancialHistoryDAO().batch_save([{
        "stock_code": "600519", "report_date": "2024-12-31",
        "notice_date": "2025-04-01", "fcf": 100.0, "total_shares": 10.0,
        "cash": 20.0, "interest_bearing_debt": 50.0,
    }])
    fin = FinancialHistoryDAO()
    mos = _mos_at_entry(fin, "600519", "2025-05-01", 12.0)
    assert mos is not None and mos > 0
    # 披露日晚于分析日 → 不可用（防前视）
    assert _mos_at_entry(fin, "600519", "2025-03-01", 12.0) is None


def test_summarize_by_mos_tiers(db):
    from src.models.database import AnalysisOutcomeDAO
    rows = []
    for i, mos in enumerate([-10, 10, 30, 50]):
        rows.append({
            "analysis_id": i + 1, "benchmark_code": "sh000300",
            "stock_code": f"00000{i}", "stock_name": "x",
            "analysis_date": "2025-01-01", "signal": "BUY",
            "confidence": "中", "verdict": "通过",
            "entry_date": "2025-01-02", "entry_price": 10.0,
            "mos_at_entry": mos,
            "ret_20": 5.0, "ret_60": 8.0,
            "bench_20": 2.0, "bench_60": 3.0,
            "excess_20": 3.0, "excess_60": 5.0,
        })
    AnalysisOutcomeDAO().upsert_many(rows)
    out = summarize_by_mos("sh000300")
    by_tier = {s["tier"]: s for s in out}
    assert by_tier["0~20"]["n"] == 1
    assert by_tier["20~40"]["n"] == 1
    assert by_tier["40~100"]["n"] == 1
    assert by_tier["-100~0"]["n"] == 1
    assert by_tier["20~40"]["avg_ret_20"] == 5.0
