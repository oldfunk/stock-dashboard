"""批16-a 用例（2026-11-05）：东财 F10 两张表改为"仅缺字段才拉"。

产线日志实测：平日增量 todo 高峰期 737 只/天，每只发 3 个请求（同花顺骨架 1 +
东财利润表 1 + 东财现金流量表 1），而两张表的产物 interest_coverage/fcf 多数早就
填过——重复拉只是白花配额（15-b 激活之后才成为问题）。
本文件钉四件事：
  1) 判据本身（_em_sheets_needed）：新期算缺、老期不绑架、骨架空了不拉；
  2) 增量真跳过（不发请求）且**不会把已填的值抹成 NULL**；
  3) force_full / onboard 保持全量（周六是修老数据的手段）；
  4) 省了多少请求要能在日志里读到，不靠推断。
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


def _abs_frame(dates):
    """同花顺骨架帧：每行一个报告期（顺序刻意可以乱，判定不该依赖它）。"""
    rows = []
    for d in sorted(dates, reverse=True):
        rows.append({c: None for c in ABS_COLS}
                    | {'报告期': d, '基本每股收益': 1.0, '净利润': 100.0,
                       '净资产收益率': '10.0'})
    return pd.DataFrame(rows)


def _seed(code, dates, *, ic=True, fcf=True):
    """往 financial_history 写若干期；ic/fcf=False 表示该字段留空（=还缺）。"""
    recs = []
    for d in dates:
        rec = {'stock_code': code, 'report_date': d, 'roe': 10.0, 'eps': 1.0,
               'net_profit': 100.0}
        if ic:
            rec['interest_coverage'] = 9.0
            rec['total_shares'] = 1_000_000.0
        if fcf:
            rec['fcf'] = 700.0
        recs.append(rec)
    db_mod.FinancialHistoryDAO().batch_save(recs)


def _deep(code):
    with db_mod.db_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT report_date, interest_coverage, fcf FROM financial_history"
            " WHERE stock_code=? ORDER BY report_date", (code,)).fetchall()]


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "b16a.db"
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


def _install_ak(monkeypatch, cap, abs_dates=("2024-12-31", "2024-09-30"),
                abs_empty=False):
    """假 akshare：分别记录同花顺 / 利润表 / 现金流量表被调了几次、传了什么。"""
    ak = types.ModuleType("akshare")

    def _abs(symbol=None, **kw):
        cap.setdefault("abs", []).append(symbol)
        return pd.DataFrame([]) if abs_empty else _abs_frame(abs_dates)

    def _ps(symbol=None, **kw):
        cap.setdefault("ps", []).append(symbol)
        return pd.DataFrame([{"REPORT_DATE": d, "OPERATE_PROFIT": 90.0,
                              "INTEREST_EXPENSE": 10.0, "TOTAL_SHARES": 2e6,
                              "NETPROFIT": 100.0, "OPERATE_INCOME": 9000.0}
                             for d in abs_dates])

    def _cf(symbol=None, **kw):
        cap.setdefault("cf", []).append(symbol)
        return pd.DataFrame([{"REPORT_DATE": d, "NETCASH_OPERATE": 800.0,
                              "NETCASH_INVEST": -100.0} for d in abs_dates])

    ak.stock_financial_abstract_ths = _abs
    ak.stock_profit_sheet_by_report_em = _ps
    ak.stock_cash_flow_sheet_by_report_em = _cf
    monkeypatch.setitem(sys.modules, "akshare", ak)


# ── 1) 判据本身 ───────────────────────────────────────────

@pytest.mark.parametrize("dates,filled,expect", [
    # 骨架没出数：两张表都不该拉（旧写法照样发 2 个请求，返回值没人消费）
    ([], {"interest_coverage": set(), "fcf": set()}, (False, False)),
    # 近 2 期都已填 → 都跳
    (["2024-12-31", "2024-09-30"],
     {"interest_coverage": {"2024-12-31", "2024-09-30"},
      "fcf": {"2024-12-31", "2024-09-30"}}, (False, False)),
    # 新披露一期（库里没有这行）→ 两张表都要拉，这正是增量想要的
    (["2025-03-31", "2024-12-31"],
     {"interest_coverage": {"2024-12-31"}, "fcf": {"2024-12-31"}}, (True, True)),
    # 只有 interest_coverage 缺 → 只拉利润表
    (["2024-12-31", "2024-09-30"],
     {"interest_coverage": {"2024-12-31"}, "fcf": {"2024-12-31", "2024-09-30"}},
     (True, False)),
    # 只有 fcf 缺 → 只拉现金流量表
    (["2024-12-31", "2024-09-30"],
     {"interest_coverage": {"2024-12-31", "2024-09-30"},
      "fcf": {"2024-12-31"}}, (False, True)),
    # 窗口外的老期缺数不许绑架请求（东财 2017 年及以前利息费用覆盖仅 12.4%）：
    # 12 期里最近 8 期填满、更早 4 期永远填不满 → 仍应两个都跳
    ([f"20{y:02d}-12-31" for y in range(13, 25)],
     {"interest_coverage": {f"20{y:02d}-12-31" for y in range(16, 25)},
      "fcf": {f"20{y:02d}-12-31" for y in range(16, 25)}}, (False, False)),
])
def test_gate_predicate(dates, filled, expect):
    from src.collector.akshare_fetcher import _em_sheets_needed
    assert _em_sheets_needed(dates, filled) == expect


def test_gate_does_not_depend_on_frame_order():
    """报告期乱序进来也必须判对——同花顺返回顺序不是契约。"""
    from src.collector.akshare_fetcher import _em_sheets_needed
    filled = {"interest_coverage": {"2024-12-31", "2024-09-30"},
              "fcf": {"2024-12-31", "2024-09-30"}}
    assert _em_sheets_needed(["2024-09-30", "2024-12-31"], filled) == (False, False)


# ── 2) 增量真的不发请求，且不清空已填值 ───────────────────

def test_incremental_skips_both_em_sheets_when_fields_present(env, monkeypatch):
    _seed("600519", ["2024-12-31", "2024-09-30"])
    cap = {}
    _install_ak(monkeypatch, cap)
    env.collect_historical_financial_data([{"code": "600519"}])
    assert cap["abs"] == ["600519"], "骨架照常要拉（新期要靠它发现）"
    assert "ps" not in cap, f"利息覆盖已填满，仍发了利润表请求：{cap.get('ps')}"
    assert "cf" not in cap, f"FCF 已填满，仍发了现金流量表请求：{cap.get('cf')}"


def test_skipping_does_not_wipe_existing_deep_values(env, monkeypatch):
    """跳过 ≠ 覆盖：batch_save 逐列 COALESCE，这次没拉不许把已填的抹成 NULL。"""
    _seed("600519", ["2024-12-31", "2024-09-30"])
    cap = {}
    _install_ak(monkeypatch, cap)
    env.collect_historical_financial_data([{"code": "600519"}])
    rows = _deep("600519")
    assert [r["interest_coverage"] for r in rows] == [9.0, 9.0]
    assert [r["fcf"] for r in rows] == [700.0, 700.0]


def test_new_report_period_still_pulls_both_sheets(env, monkeypatch):
    """新披露一期（库里没这行）→ 两张表都要拉，这是增量存在的理由。"""
    _seed("600519", ["2024-12-31", "2024-09-30"])
    cap = {}
    _install_ak(monkeypatch, cap, abs_dates=("2025-03-31", "2024-12-31", "2024-09-30"))
    env.collect_historical_financial_data([{"code": "600519"}])
    assert cap["ps"] == [em_f10_symbol("600519")]
    assert cap["cf"] == [em_f10_symbol("600519")]


def test_only_interest_coverage_missing_pulls_profit_sheet_only(env, monkeypatch):
    _seed("600519", ["2024-12-31", "2024-09-30"], ic=False)
    cap = {}
    _install_ak(monkeypatch, cap)
    env.collect_historical_financial_data([{"code": "600519"}])
    assert cap.get("ps") == [em_f10_symbol("600519")]
    assert "cf" not in cap, "FCF 齐了还发请求 = 开关没起作用"


def test_only_fcf_missing_pulls_cashflow_sheet_only(env, monkeypatch):
    _seed("600519", ["2024-12-31", "2024-09-30"], fcf=False)
    cap = {}
    _install_ak(monkeypatch, cap)
    env.collect_historical_financial_data([{"code": "600519"}])
    assert cap.get("cf") == [em_f10_symbol("600519")]
    assert "ps" not in cap, "利息覆盖齐了还发利润表请求 = 开关没起作用"


def test_skeleton_failure_pulls_no_em_sheet(env, monkeypatch):
    """同花顺骨架空/挂：两张表本来就没有消费者，一个请求都不该发。"""
    _seed("600519", ["2024-12-31"], ic=False, fcf=False)
    cap = {}
    _install_ak(monkeypatch, cap, abs_empty=True)
    env.collect_historical_financial_data([{"code": "600519"}])
    assert cap.get("abs") == ["600519"]
    assert "ps" not in cap and "cf" not in cap


# ── 3) force_full 保持全量 ────────────────────────────────

def test_force_full_ignores_the_gate(env, monkeypatch):
    """周六全量/onboard 必须照旧两张表都拉——修老数据靠的就是它。"""
    _seed("600519", ["2024-12-31", "2024-09-30"])
    cap = {}
    _install_ak(monkeypatch, cap)
    env.collect_historical_financial_data([{"code": "600519"}], force_full=True)
    assert cap["ps"] == [em_f10_symbol("600519")]
    assert cap["cf"] == [em_f10_symbol("600519")]


def test_incremental_still_writes_whatever_em_returns(env, monkeypatch):
    """拉到了就要写进去：跳过逻辑不能顺带把补充路径改掉。"""
    _seed("600519", ["2024-12-31", "2024-09-30"], ic=False, fcf=False)
    cap = {}
    _install_ak(monkeypatch, cap)
    env.collect_historical_financial_data([{"code": "600519"}])
    got = {r["report_date"]: r for r in _deep("600519")}
    assert got["2024-12-31"]["interest_coverage"] == 9.0      # 90 / 10
    assert got["2024-12-31"]["fcf"] == 700.0                   # 800 + (-100)
    assert got["2024-09-30"]["interest_coverage"] == 9.0


# ── 4) 省了多少请求要能被日志证明 ─────────────────────────

def test_skip_counts_are_logged(env, monkeypatch):
    """没有这行日志，"省了多少"就只能靠推断（产线效果证据就没了）。"""
    _seed("600519", ["2024-12-31", "2024-09-30"])
    cap = {}
    _install_ak(monkeypatch, cap)
    logged = []

    class Spy:
        def warning(self, msg, *a):
            logged.append(msg % a if a else str(msg))

        def info(self, msg, *a):
            logged.append(msg % a if a else str(msg))

        def debug(self, msg, *a):
            pass

        def error(self, msg, *a):
            logged.append(msg % a if a else str(msg))

    monkeypatch.setattr(env, "logger", Spy())
    env.collect_historical_financial_data([{"code": "600519"}])
    line = [m for m in logged if "增量省请求" in m]
    assert line and "跳过东财利润表 1 只" in line[0] and "现金流量表 1 只" in line[0]


# ── DAO：判定要的集合从哪来 ───────────────────────────────

def test_get_deep_field_dates_reports_periods_not_booleans(env):
    """返回**报告期集合**：新出一期时布尔会撒谎，集合天然把新期算成缺。"""
    from src.collector.akshare_fetcher import EM_DEEP_FIELDS
    _seed("600519", ["2024-12-31", "2024-09-30"], fcf=False)
    _seed("000001", ["2024-12-31"])
    dao = db_mod.FinancialHistoryDAO()
    out = dao.get_deep_field_dates(["600519", "000001", "920000"], EM_DEEP_FIELDS)
    assert out["600519"]["interest_coverage"] == {"2024-12-31", "2024-09-30"}
    assert out["600519"]["fcf"] == set(), "fcf 留空的期不能算已填"
    assert out["000001"] == {"interest_coverage": {"2024-12-31"},
                             "fcf": {"2024-12-31"}}
    assert "920000" not in out, "库里没这只票就不该出现在结果里"
    assert dao.get_deep_field_dates([], EM_DEEP_FIELDS) == {}
    # fields 是必填：调用方漏传就 TypeError，而不是悄悄退回一份内置默认清单
    with pytest.raises(TypeError):
        dao.get_deep_field_dates(["600519"])


# ── 批17-a：Hermes H3 两条非阻断建议 ─────────────────────

@pytest.mark.parametrize("window", [0, -3])
def test_window_not_positive_is_clamped_to_one_period_not_full_history(window):
    """H3 建议①：window<=0 以前退化成"全历史参与判定"，那是开关最没用的形状。

    全历史里一定有空期（产线实测 ic 在 2017 年及以前覆盖 12.4%）→ 退化成"每天都拉"。
    钳到最少 1 期之后：最新一期已填满 ⇒ 不拉，尽管 2016 那期是空的。
    """
    from src.collector.akshare_fetcher import _em_sheets_needed
    dates = ["2024-12-31", "2016-12-31"]
    filled = {"interest_coverage": {"2024-12-31"}, "fcf": {"2024-12-31"}}
    assert _em_sheets_needed(dates, filled, window=window) == (False, False)
    # 窗口=1 时最新一期是空的 → 必须拉（钳制没把判定变成永远不拉）
    assert _em_sheets_needed(dates, {"interest_coverage": set(),
                                     "fcf": set()}, window=window) == (True, True)


def test_dao_query_and_predicate_use_one_field_list(env):
    """H3 建议②防漂移：判据用的字段名与 DAO 查的列必须是同一份清单。

    采集侧把 `EM_DEEP_FIELDS` 传给 DAO，DAO 返回的键就由它决定；这条用例把
    "两边各写一份、日后各漂各的"这条路堵死——漏同步时这里先红。
    """
    from src.collector.akshare_fetcher import EM_DEEP_FIELDS
    dao = db_mod.FinancialHistoryDAO()
    assert set(EM_DEEP_FIELDS) <= set(dao.DEEP_FIELD_COLUMNS), \
        "EM_DEEP_FIELDS 里有 DAO 白名单外的列 → 增量判定会直接抛 ValueError"
    _seed("600519", ["2024-12-31"])
    out = dao.get_deep_field_dates(["600519"], EM_DEEP_FIELDS)
    assert set(out["600519"]) == set(EM_DEEP_FIELDS)


@pytest.mark.parametrize("bad", ["password", "1=1; DROP TABLE financial_history",
                                 "interest_coverage, fcf"])
def test_dao_rejects_fields_outside_the_whitelist(env, bad):
    """列名进 f-string SQL，白名单是唯一防线（批8 P1-2 的同一条规矩）。"""
    with pytest.raises(ValueError):
        db_mod.FinancialHistoryDAO().get_deep_field_dates(["600519"], (bad,))
