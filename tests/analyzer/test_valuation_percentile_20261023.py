"""P0-5 历史估值分位回归测试（2026-10-23）。

- PE = price/EPS；PB = price/BVPS；股息率 = 每股分红/price
- 每交易日取 notice_date <= 当日 的最新年报（避免前视）
- 分位 = 当前值在历史窗口中的百分位
"""

import pytest

from src.models import database as db_mod
from src.analyzer.valuation_percentile import compute_percentiles


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "v.db"))
    db_mod.init_database()
    return db_mod


def _kline(code, prices):
    from src.models.database import KlineDAO
    KlineDAO().upsert_many(code, [{
        "trade_date": d, "open": c, "close": c, "high": c, "low": c,
        "volume": 0, "amount": 0, "turnover": 0,
    } for d, c in prices])


def _report(code, report_date, eps, shares, equity, notice):
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO financial_history "
            "(stock_code, report_date, report_type, eps, total_shares, total_equity, "
            "notice_date, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (code, report_date, "A", eps, shares, equity, notice,
             "2026-10-23T00:00:00"),
        )


def _div(code, cash):
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dividend_annual "
            "(stock_code, report_date, cash_per_share, source, updated_at) "
            "VALUES (?,?,?,?,?)",
            (code, "2018-12-31", cash, "test", "2026-10-23T00:00:00"),
        )


def test_percentiles_monotonic_prices(db):
    # 6 年价格单调上涨，EPS/BVPS 恒定 → 最新 PE/PB 最高、股息率最低
    prices = [(f"{y}-{m:02d}-15", float(5 + (y - 2019) * 2))
              for y in range(2019, 2025) for m in range(1, 13)]
    _kline("600519", prices)
    _report("600519", "2018-12-31", eps=1.0, shares=100, equity=500,
            notice="2019-01-01")
    _div("600519", 0.05)
    out = compute_percentiles("600519")
    assert "5" in out and "10" in out
    assert out["5"]["pe_pct"] == 100.0
    assert out["5"]["pb_pct"] == 100.0
    # 价格逐年上涨 → 最新 PE/PB 最高（分位 100），股息率最低（分位低）
    assert out["5"]["dy_pct"] < out["5"]["pe_pct"]
    assert out["5"]["dy_pct"] == 19.7  # 最新 12 个月并列最低，占 61 点窗口的 19.7%


def test_no_lookahead(db):
    # 披露日晚于价格日 → 该年报不得用于该日
    _kline("600519", [("2024-01-15", 10.0), ("2024-12-15", 12.0)])
    _report("600519", "2023-12-31", eps=1.0, shares=100, equity=500,
            notice="2024-06-01")
    out = compute_percentiles("600519")
    # 2024-01-15 早于披露日 → 该日无可用年报；窗口样本不足 30 → 无结果
    assert out == {}


def test_report_without_notice_date_is_not_used(db):
    """缺披露日不得回退 report_date（否则前视约 3 个月）。"""
    prices = [(f"2023-{m:02d}-15", 10.0) for m in range(1, 13)]
    prices += [(f"2024-{m:02d}-15", 10.0) for m in range(1, 4)]
    _kline("600519", prices)
    _report("600519", "2022-12-31", eps=1.0, shares=100, equity=500, notice=None)
    assert compute_percentiles("600519") == {}


def test_shares_at_prefers_derived_historical_shares():
    """历史年份用 net_profit/eps 反推股本；反推不出则为 None（不拿当前股本硬算）。"""
    from src.analyzer.valuation_percentile import _shares_at
    old = {"report_date": "2020-12-31", "net_profit": 500.0, "eps": 1.0,
           "total_shares": 1000.0}
    assert _shares_at(old, "2025-12-31") == 500.0
    unknown = {"report_date": "2020-12-31", "net_profit": None, "eps": None,
               "total_shares": 1000.0}
    assert _shares_at(unknown, "2025-12-31") is None
    latest = {"report_date": "2025-12-31", "net_profit": None, "eps": None,
              "total_shares": 1000.0}
    assert _shares_at(latest, "2025-12-31") == 1000.0


def test_insufficient_data_returns_empty(db):
    assert compute_percentiles("999999") == {}
