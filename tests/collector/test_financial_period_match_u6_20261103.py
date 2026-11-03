"""U6 复核用例（2026-11-03）：利润表/现金流量表补充**只按同一报告期**精确匹配。

复核结论：机制成立，而且比原条目更糟——见交付帖：
1) 旧兜底 `ps_d[:4] == d[:4] or (同年 and 月份<=)` 是恒等式（等价于"同年任意一期"），
   且 `sorted(reverse=True)` 取的是**年内最新**一期：2024-09-30 的年初至今累计值
   会盖进 2024-12-31 年报行和 2024-03-31 一季报行（利润表/现金流都是累计口径，
   跨期即错值，`total_operate_reve` 还是无条件覆盖）。
2) 同一次取证还发现 `stock_profit_sheet_by_report_em(symbol="600519")` 在产线
   直接抛错（该接口要 `SH600519` 形式），所以这两段补充今天**从来没执行过**
   ——本件的修法防的是这条路一旦被修好后立刻会踩的坑。
"""

import sys
import types

import pandas as pd
import pytest

from src.models import database as db_mod

ABS_COLS = ['报告期', '净资产收益率', '销售毛利率', '销售净利率', '每股经营现金流',
             '资产负债率', '基本每股收益', '净利润', '营业总收入同比增长率',
             '净利润同比增长率', '营业总收入']


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "u6.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()

    import src.collector.akshare_fetcher as f
    monkeypatch.setattr(f.time, "sleep", lambda s: None)
    # 关掉其它补数据通路，让断言只反映本次修改的那两段
    monkeypatch.setattr(f, "_fetch_eastmoney_roic_fcf", lambda code: {})
    import src.collector.balance_sheet_fetcher as bs
    import src.collector.cashflow_fetcher as cfl
    monkeypatch.setattr(bs, "fetch_balance_sheet_annual", lambda code: {})
    monkeypatch.setattr(cfl, "fetch_cash_flow_annual", lambda code: {})
    return f


def _patch_ak(monkeypatch, *, abstract, profit, cashflow):
    ak = types.ModuleType("akshare")
    ak.stock_financial_abstract_ths = lambda symbol=None, **kw: abstract
    ak.stock_profit_sheet_by_report_em = lambda symbol=None, **kw: profit
    ak.stock_cash_flow_sheet_by_report_em = lambda symbol=None, **kw: cashflow
    monkeypatch.setitem(sys.modules, "akshare", ak)


def _abs_row(rpt, *, eps=None, net_profit=None, roe="10.0"):
    return {c: None for c in ABS_COLS} | {'报告期': rpt, '基本每股收益': eps,
                                          '净利润': net_profit, '净资产收益率': roe}


def _ps_row(rpt, *, op=90.0, ie=10.0, shares=1_000_000.0, np_=900.0, rev=9_000.0):
    return {'REPORT_DATE': rpt, 'OPERATE_PROFIT': op, 'INTEREST_EXPENSE': ie,
            'TOTAL_SHARES': shares, 'NETPROFIT': np_, 'OPERATE_INCOME': rev}


def _cf_row(rpt, *, ocf=800.0, inv=-100.0):
    return {'REPORT_DATE': rpt, 'NETCASH_OPERATE': ocf, 'NETCASH_INVEST': inv}


def _row(code, rpt):
    with db_mod.db_conn() as conn:
        r = conn.execute("SELECT * FROM financial_history WHERE stock_code=?"
                         " AND report_date=?", (code, rpt)).fetchone()
    return dict(r) if r else None


def test_q3_cumulative_does_not_leak_into_annual_row(env, monkeypatch):
    """摘要里有 2024-12-31，但利润表只有 2024-09-30：年报行必须保持 NULL，不拿季报到账。"""
    abstract = pd.DataFrame([_abs_row('2024-12-31')])
    profit = pd.DataFrame([_ps_row('2024-09-30')])
    flow = pd.DataFrame([_cf_row('2024-09-30')])
    _patch_ak(monkeypatch, abstract=abstract, profit=profit, cashflow=flow)

    env.collect_historical_financial_data([{"code": "600519"}], force_full=True)
    row = _row("600519", "2024-12-31")
    assert row is not None, "年报行本身要落库"
    for col in ("interest_coverage", "total_shares", "net_profit", "fcf"):
        assert row[col] is None, f"{col} 被 2024-09-30 的累计值盖进了年报行"


def test_q1_row_does_not_take_q3_values(env, monkeypatch):
    """一季报行同理：03-31 不许被同年的 09-30 覆盖（旧兜底取年内最新一期）。"""
    abstract = pd.DataFrame([_abs_row('2024-03-31')])
    profit = pd.DataFrame([_ps_row('2024-09-30')])
    _patch_ak(monkeypatch, abstract=abstract, profit=profit,
              cashflow=pd.DataFrame([]))

    env.collect_historical_financial_data([{"code": "000001"}], force_full=True)
    row = _row("000001", "2024-03-31")
    assert row is not None
    assert row["interest_coverage"] is None and row["total_shares"] is None


def test_exact_report_date_still_enriches(env, monkeypatch):
    """精确匹配那条路不能被一起删掉：同报告期照旧补全。"""
    abstract = pd.DataFrame([_abs_row('2024-12-31')])
    profit = pd.DataFrame([_ps_row('2024-12-31', op=90.0, ie=10.0,
                                   shares=1_000_000.0)])
    flow = pd.DataFrame([_cf_row('2024-12-31', ocf=800.0, inv=-100.0)])
    _patch_ak(monkeypatch, abstract=abstract, profit=profit, cashflow=flow)

    env.collect_historical_financial_data([{"code": "300750"}], force_full=True)
    row = _row("300750", "2024-12-31")
    assert row["interest_coverage"] == 9.0
    assert row["total_shares"] == 1_000_000.0
    assert row["fcf"] == 700.0          # 800 + (-100)


def test_missing_income_sheet_entirely_leaves_row_untouched(env, monkeypatch):
    """利润表整体为空（产线现状：接口按裸代码调用直接抛错）→ 不编造任何值。"""
    abstract = pd.DataFrame([_abs_row('2024-12-31')])
    _patch_ak(monkeypatch, abstract=abstract, profit=pd.DataFrame([]),
              cashflow=pd.DataFrame([]))
    env.collect_historical_financial_data([{"code": "600000"}], force_full=True)
    row = _row("600000", "2024-12-31")
    assert row["interest_coverage"] is None and row["fcf"] is None
