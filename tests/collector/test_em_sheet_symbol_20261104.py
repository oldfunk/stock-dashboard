"""15-b 用例（2026-11-04）：东财 F10 两接口的 symbol 形态 + 失败必须留痕。

现状（产线取证）：`stock_profit_sheet_by_report_em` / `stock_cash_flow_sheet_by_report_em`
被传裸 6 位代码 → 每次 `TypeError: 'NoneType' object is not subscriptable` →
异常被**静默** except 吞掉 → 四段补充（利息覆盖/总股本/FCF/每股经营现金流）
从来没执行过。本文件钉三件事：前缀形态、同花顺仍是裸码（别顺手改错）、
失败要进日志。
"""

import sys
import types

import pandas as pd
import pytest

from src.models import database as db_mod
from src.utils import em_f10_symbol

ABS_COLS = ['报告期', '净资产收益率', '销售毛利率', '销售净利率', '每股经营现金流',
             '资产负债率', '基本每股收益', '净利润', '营业总收入同比增长率',
             '净利润同比增长率', '营业总收入']

SAMPLES = ["600519", "000001", "920000", "300750", "002594"]


def _abs_frame(rpt="2024-12-31"):
    row = {c: None for c in ABS_COLS} | {'报告期': rpt, '基本每股收益': 1.0,
                                         '净利润': 100.0, '净资产收益率': '10.0'}
    return pd.DataFrame([row])


def _empty():
    return pd.DataFrame([])


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "b15.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    import src.collector.akshare_fetcher as f
    monkeypatch.setattr(f.time, "sleep", lambda s: None)
    monkeypatch.setattr(f, "_fetch_eastmoney_roic_fcf", lambda code: {})
    import src.collector.balance_sheet_fetcher as bs
    import src.collector.cashflow_fetcher as cfl
    monkeypatch.setattr(bs, "fetch_balance_sheet_annual", lambda code: {})
    monkeypatch.setattr(cfl, "fetch_cash_flow_annual", lambda code: {})
    return f


def _install_ak(monkeypatch, capture, *, sheets_ok=True):
    """假 akshare：记录每次调用的 (接口, symbol)，返回按前缀形态取数的帧。"""
    ak = types.ModuleType("akshare")

    def _abs(symbol=None, **kw):
        capture.setdefault("abs", []).append(symbol)
        return _abs_frame()

    def _sheet(symbol=None, **kw):
        capture.setdefault("sheets", []).append(symbol)
        if not sheets_ok:
            raise TypeError("'NoneType' object is not subscriptable")
        # 只有带市场前缀的形态才给数据——这正是 akshare 的真实行为
        if not symbol[:2].isupper():
            raise TypeError("'NoneType' object is not subscriptable")
        return pd.DataFrame([{"REPORT_DATE": "2024-12-31",
                              "OPERATE_PROFIT": 90.0, "INTEREST_EXPENSE": 10.0,
                              "TOTAL_SHARES": 1_000_000.0, "NETPROFIT": 100.0,
                              "OPERATE_INCOME": 9_000.0,
                              "NETCASH_OPERATE": 800.0, "NETCASH_INVEST": -100.0}])

    ak.stock_financial_abstract_ths = _abs
    ak.stock_profit_sheet_by_report_em = _sheet
    ak.stock_cash_flow_sheet_by_report_em = _sheet
    monkeypatch.setitem(sys.modules, "akshare", ak)


@pytest.mark.parametrize("code", SAMPLES)
def test_em_sheets_are_called_with_f10_prefix_symbol(env, monkeypatch, code):
    """两个东财接口必须拿到 SH/SZ/BJ + 6 位码；同花顺继续拿裸码。"""
    cap = {}
    _install_ak(monkeypatch, cap)
    env.collect_historical_financial_data([{"code": code}], force_full=True)
    assert cap["sheets"] == [em_f10_symbol(code)] * 2, \
        f"{code} 应两次都传 {em_f10_symbol(code)}，实得 {cap['sheets']}"
    assert cap["abs"] == [code], "同花顺接口的正解是裸 6 位代码，不能顺手改"


def test_prefix_form_actually_enriches_and_bare_form_did_not(env, monkeypatch):
    """前缀形态下四段补充真的写进库；这就是"从来没执行过"的反证。"""
    cap = {}
    _install_ak(monkeypatch, cap)
    env.collect_historical_financial_data([{"code": "920000"}], force_full=True)
    with db_mod.db_conn() as conn:
        row = dict(conn.execute(
            "SELECT * FROM financial_history WHERE stock_code='920000'"
            " AND report_date='2024-12-31'").fetchone())
    assert row["interest_coverage"] == 9.0
    assert row["total_shares"] == 1_000_000.0
    assert row["fcf"] == 700.0                      # 800 + (-100)


def test_sheet_failure_is_logged_not_swallowed(env, monkeypatch):
    """静默是这次能瞒这么久的主因：接口挂掉必须留下 warning。"""
    cap = {}
    _install_ak(monkeypatch, cap, sheets_ok=False)
    import src.collector.akshare_fetcher as s
    logged = []

    class Spy:
        def warning(self, msg, *a):
            logged.append(msg % a if a else str(msg))

        def info(self, msg, *a):
            pass

        def debug(self, msg, *a):
            pass

        def error(self, msg, *a):
            logged.append(msg % a if a else str(msg))

    monkeypatch.setattr(s, "logger", Spy())
    env.collect_historical_financial_data([{"code": "600519"}], force_full=True)
    text = " | ".join(logged)
    assert "利润表" in text and "现金流量表" in text, f"两路失败都要留痕：{text}"
    # 摘要仍成功 → 年报行照写，但补充列为 NULL（fail-closed，不臆造）
    with db_mod.db_conn() as conn:
        row = dict(conn.execute(
            "SELECT interest_coverage, fcf FROM financial_history"
            " WHERE stock_code='600519' AND report_date='2024-12-31'").fetchone())
    assert row["interest_coverage"] is None and row["fcf"] is None
