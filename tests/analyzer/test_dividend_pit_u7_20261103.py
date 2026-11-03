"""U7 复核用例（2026-11-03）：股息率序列必须是 PIT 的，不是把今天的分红套满全历史。

原实现 `dy = 最新一期 cash_per_share / 当日价`，于是整条 dy 序列 = 常数 / 价格：
它的"分位"就是**价格分位的倒数**，与"股息率处于历史什么位置"无关。
分红会增派/减派/停派，所以必须按"当日已披露的最近一期"取数（与年报同一条规则）。
"""

import pytest

from src.models import database as db_mod
from src.analyzer.valuation_percentile import compute_percentiles, _cash_at, _dividends


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "v.db"))
    db_mod.init_database()
    return db_mod


def _kline(code, prices):
    from src.models.database import KlineDAO
    KlineDAO().upsert_many(code, [{
        "trade_date": d, "open": c, "close": c, "high": c, "low": c,
        "volume": 0, "amount": 0,
    } for d, c in prices])


def _report(code, report_date, eps=1.0, shares=100, equity=500, notice=None):
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO financial_history "
            "(stock_code, report_date, report_type, eps, total_shares, "
            " total_equity, notice_date, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (code, report_date, "A", eps, shares, equity, notice,
             "2026-11-03T00:00:00"))


def _div(code, report_date, cash):
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dividend_annual "
            "(stock_code, report_date, cash_per_share, source, updated_at) "
            "VALUES (?,?,?,?,?)",
            (code, report_date, cash, "test", "2026-11-03T00:00:00"))


def _days(year, months, price):
    return [(f"{year}-{m:02d}-15", float(price)) for m in months]


# ── 单元：_cash_at 的 PIT 语义 ────────────────────────────────

def test_cash_at_uses_only_dividends_disclosed_by_that_day(db):
    _report("600519", "2020-12-31", notice="2021-04-30")
    _report("600519", "2022-12-31", notice="2023-04-30")
    _div("600519", "2020-12-31", 2.0)
    _div("600519", "2022-12-31", 0.4)
    divs = _dividends("600519")
    assert _cash_at(divs, "2021-01-15") is None       # 两份都还没披露
    assert _cash_at(divs, "2021-05-15") == 2.0        # FY2020 已披露
    assert _cash_at(divs, "2023-03-15") == 2.0        # FY2022 尚未披露
    assert _cash_at(divs, "2023-05-15") == 0.4        # 减派后


def test_dividend_row_without_notice_is_not_used(db):
    """与年报同一条规则：缺披露日的分红行不参与（不回退 report_date）。"""
    _div("600000", "2021-12-31", 1.0)          # 没有对应 financial_history 行
    assert _cash_at(_dividends("600000"), "2026-01-15") is None


# ── 端到端：分红减派时，分位不再等于价格分位的倒数 ───────────

def test_dy_percentile_tracks_dividend_change_not_price_only(db):
    """FY2020 派 2.0、FY2022 减派到 0.4，价格 100 → 80。

    PIT 下的股息率序列（EPS 恒定，故 PE 只看价）：
      2021-01..04 = None（尚无已披露分红，4 点）
      2021-05..12 = 2.0/100 = 2.0（8 点）
      2022 全年   = 2.0/100 = 2.0（12 点）
      2023-01..04 = 2.0/80 = 2.5（4 点，FY2022 还没披露）
      2023-05..12 = 0.4/80 = 0.5（8 点）
    当前值 0.5，32 个有效点里 8 个 <= 0.5 → **dy_pct = 25.0**；
    PE 序列 36 点（24×100、12×80），当前 80 → **pe_pct = 33.3**。
    旧口径把"库里最新一条"(0.4) 套满全历史 → dy = 0.4/0.5 两级且当前值是最大值
    → dy_pct 会是 **100.0**："股息率处于历史最高位"其实全是股价下跌的倒影。
    """
    prices = _days(2021, range(1, 13), 100) + _days(2022, range(1, 13), 100) \
        + _days(2023, range(1, 13), 80)
    _kline("600519", prices)
    _report("600519", "2018-12-31", notice="2019-04-30")
    _report("600519", "2020-12-31", notice="2021-04-30")
    _report("600519", "2022-12-31", notice="2023-04-30")
    _div("600519", "2020-12-31", 2.0)
    _div("600519", "2022-12-31", 0.4)

    out = compute_percentiles("600519", years=(5,))
    assert out["5"]["n"] == 36
    assert out["5"]["pe_pct"] == 33.3
    assert out["5"]["dy_pct"] == 25.0


def test_dy_is_none_when_no_dividend_disclosed_yet_but_pe_still_computed(db):
    """整个窗口都没有已披露的分红 → dy_pct 为 None，但 PE/PB 照常（不因一侧缺数整体失效）。"""
    prices = _days(2021, range(1, 13), 100) + _days(2022, range(1, 13), 90) \
        + _days(2023, range(1, 13), 80)
    _kline("600000", prices)
    _report("600000", "2018-12-31", notice="2019-04-30")
    # 唯一一条分红属于 FY2022，但其披露日在 2026 年，晚于全部价格日 → 窗口内不可用
    _report("600000", "2022-12-31", notice="2026-04-30")
    _div("600000", "2022-12-31", 1.0)
    out = compute_percentiles("600000", years=(5,))
    assert out["5"]["dy_pct"] is None
    assert out["5"]["pe_pct"] is not None
