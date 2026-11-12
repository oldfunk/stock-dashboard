"""批19-a 用例（2026-11-12）：陈旧度规则收录"近窗口深层字段缺失"+ EM 冷却。

派工单的两条验收：
  1) 最新报告期在 60 天内、但近 8 期 interest_coverage 缺失 → todo 为 True；
  2) 深层字段齐全 → todo 为 False（批16-a 的省量成果不倒退）。

取证补充（产线只读，命令见交付帖）：东财利润表 ``INTEREST_EXPENSE`` 对非金融股
**恒为 NaN**（605259 0/31 期、000983 0/108 期），所以采集侧 ``op/abs(ie)`` 永远
算不出 ic。19-a 单独上线会让 771 只候选**全部**入选（实测 0 → 771，2313 请求/天）
且缺口永不闭合 —— 因此母方 2026-11-05 已裁定的"EM 冷却"（批18-d）必须同批落地，
本文件把冷却的四条边界一起钉住。

报告期一律相对"今天"生成：写死 2024/2026 的日期会在明年变成"过旧"，
用例语义随日历漂移就等于没钉住。
"""

import sys
import types
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.models import database as db_mod
from src.utils import em_f10_symbol

ABS_COLS = ['报告期', '净资产收益率', '销售毛利率', '销售净利率', '每股经营现金流',
            '资产负债率', '基本每股收益', '净利润', '营业总收入同比增长率',
            '净利润同比增长率', '营业总收入']


def _recent_dates(n, end_offset=10, step=90):
    """n 个报告期（新→旧），最新一期距今 end_offset 天。"""
    end = datetime.now().date() - timedelta(days=end_offset)
    return [(end - timedelta(days=step * i)).strftime("%Y-%m-%d")
            for i in range(n)]


def _abs_frame(dates):
    rows = []
    for d in sorted(dates, reverse=True):
        rows.append({c: None for c in ABS_COLS}
                    | {'报告期': d, '基本每股收益': 1.0, '净利润': 100.0,
                       '净资产收益率': '10.0'})
    return pd.DataFrame(rows)


def _seed(code, dates, *, ic=True, fcf=True):
    recs = []
    for d in dates:
        rec = {'stock_code': code, 'report_date': d, 'roe': 10.0, 'eps': 1.0,
               'net_profit': 100.0}
        if ic:
            rec['interest_coverage'] = 9.0
        if fcf:
            rec['fcf'] = 700.0
        recs.append(rec)
    db_mod.FinancialHistoryDAO().batch_save(recs)


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "b19a.db"
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


def _install_ak(monkeypatch, cap, dates, *, interest_expense=10.0,
                ps_error=False, abs_empty=False):
    """假 akshare。``interest_expense=None`` 复刻产线非金融股的真实形状。"""
    ak = types.ModuleType("akshare")

    def _abs(symbol=None, **kw):
        cap.setdefault("abs", []).append(symbol)
        return pd.DataFrame([]) if abs_empty else _abs_frame(dates)

    def _ps(symbol=None, **kw):
        cap.setdefault("ps", []).append(symbol)
        if ps_error:
            raise RuntimeError("上游限流")
        return pd.DataFrame([{"REPORT_DATE": d, "OPERATE_PROFIT": 90.0,
                              "INTEREST_EXPENSE": interest_expense,
                              "TOTAL_SHARES": 2e6, "NETPROFIT": 100.0,
                              "OPERATE_INCOME": 9000.0} for d in dates])

    def _cf(symbol=None, **kw):
        cap.setdefault("cf", []).append(symbol)
        return pd.DataFrame([{"REPORT_DATE": d, "NETCASH_OPERATE": 800.0,
                              "NETCASH_INVEST": -100.0} for d in dates])

    ak.stock_financial_abstract_ths = _abs
    ak.stock_profit_sheet_by_report_em = _ps
    ak.stock_cash_flow_sheet_by_report_em = _cf
    monkeypatch.setitem(sys.modules, "akshare", ak)


def _marks(code, task=None):
    from src.collector.akshare_fetcher import EM_DEEP_TASK
    with db_mod.db_conn() as conn:
        rows = conn.execute(
            "SELECT status, covered_report_date FROM backfill_progress"
            " WHERE task=? AND code=?", (task or EM_DEEP_TASK, code)).fetchall()
    return [dict(r) for r in rows]


def _ic(code, date):
    with db_mod.db_conn() as conn:
        row = conn.execute(
            "SELECT interest_coverage FROM financial_history"
            " WHERE stock_code=? AND report_date=?", (code, date)).fetchone()
    return row["interest_coverage"] if row else None


# ── 验收 1：报告期够新但深层字段缺 → 必须入选 ──────────────

def test_fresh_report_period_but_missing_ic_is_still_collected(env, monkeypatch):
    """派工单验收①：最新报告期在 60 天内、近 8 期 ic 缺失 → todo=True。"""
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    out = env.collect_historical_financial_data([{"code": "600519"}])
    assert out == {"600519": True}, "报告期新就不拉 = 2026 三期 ic 永远为空的根因"
    assert cap["ps"] == [em_f10_symbol("600519")]
    assert _ic("600519", dates[0]) == 9.0


def test_fresh_report_period_but_missing_fcf_is_still_collected(env, monkeypatch):
    """判据是 EM_DEEP_FIELDS 两列，不是只盯 ic。"""
    dates = _recent_dates(4)
    _seed("600519", dates, fcf=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    out = env.collect_historical_financial_data([{"code": "600519"}])
    assert out == {"600519": True}
    assert cap["cf"] == [em_f10_symbol("600519")]


# ── 验收 2：深层字段齐全 → 不重复拉（省量不倒退）──────────

def test_complete_deep_fields_are_not_collected_again(env, monkeypatch):
    """派工单验收②：齐全 → todo=False，一个请求都不发。"""
    dates = _recent_dates(4)
    _seed("600519", dates)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    out = env.collect_historical_financial_data([{"code": "600519"}])
    assert out == {}
    assert cap == {}, f"字段齐全还发请求 = 批16-a 的省量成果倒退：{cap}"


def test_old_period_gap_outside_window_does_not_force_todo(env, monkeypatch):
    """窗口外的老期缺数不许绑架：12 期里最近 8 期齐全 → 不入选。

    产线 ic 在 2017 年及以前覆盖只有 12.4%，不设窗口就是"每天都拉"。
    """
    dates = _recent_dates(12)
    _seed("600519", dates[:8])
    _seed("600519", dates[8:], ic=False, fcf=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    assert env.collect_historical_financial_data([{"code": "600519"}]) == {}
    assert cap == {}


def test_gap_inside_window_forces_todo_even_if_older_periods_complete(env, monkeypatch):
    """反向：最近 8 期里缺一期就要拉，老期齐全不能抵消。"""
    dates = _recent_dates(12)
    _seed("600519", dates[1:8])
    _seed("600519", [dates[0]] + dates[8:], ic=False, fcf=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    assert env.collect_historical_financial_data([{"code": "600519"}]) == {"600519": True}


# ── 旧规则不许被改坏 ──────────────────────────────────────

def test_stale_report_period_still_collected_even_when_fields_complete(env, monkeypatch):
    dates = _recent_dates(3, end_offset=100)
    _seed("600519", dates)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    assert env.collect_historical_financial_data([{"code": "600519"}]) == {"600519": True}


def test_no_history_at_all_is_collected(env, monkeypatch):
    dates = _recent_dates(2)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    assert env.collect_historical_financial_data([{"code": "920000"}]) == {"920000": True}


def test_unparseable_latest_report_date_is_collected(env, monkeypatch):
    """库里报告期形状坏 → 保守入选（旧行为，别在重构里丢掉）。"""
    _seed("600519", ["2026-07-1"])
    cap = {}
    _install_ak(monkeypatch, cap, _recent_dates(2))
    assert env.collect_historical_financial_data([{"code": "600519"}]) == {"600519": True}


# ── 批量判定：不在循环里逐只查 ────────────────────────────

def test_todo_decision_queries_are_batched_not_per_stock(env, monkeypatch):
    """产线候选池 771 只：三个判定查询各只许发一次。"""
    dates = _recent_dates(4)
    codes = [f"60{i:04d}" for i in range(12)]
    for c in codes:
        _seed(c, dates, ic=False)
    calls = {"deep": 0, "periods": 0, "marks": 0, "latest": 0}
    dao_cls = db_mod.FinancialHistoryDAO
    orig = {k: getattr(dao_cls, m) for k, m in
            (("deep", "get_deep_field_dates"), ("periods", "get_report_dates"),
             ("marks", "get_backfill_marks"), ("latest", "get_latest_report_dates"))}

    def _wrap(key, method, args_kw=None):
        def _inner(self, *a, **kw):
            calls[key] += 1
            if args_kw is not None:
                args_kw.append((a, kw))
            return method(self, *a, **kw)
        return _inner

    seen_fields = []
    monkeypatch.setattr(dao_cls, "get_deep_field_dates",
                        _wrap("deep", orig["deep"], seen_fields))
    monkeypatch.setattr(dao_cls, "get_report_dates", _wrap("periods", orig["periods"]))
    monkeypatch.setattr(dao_cls, "get_backfill_marks", _wrap("marks", orig["marks"]))
    monkeypatch.setattr(dao_cls, "get_latest_report_dates", _wrap("latest", orig["latest"]))
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    env.collect_historical_financial_data([{"code": c} for c in codes])
    assert calls == {"deep": 1, "periods": 1, "marks": 1, "latest": 1}, \
        f"判定查询被逐只发了：{calls}"
    # 判据用哪几列与 DAO 查哪几列必须是同一份清单（批17-a 的口径别破坏）
    from src.collector.akshare_fetcher import EM_DEEP_FIELDS
    assert seen_fields[0][0][1] == EM_DEEP_FIELDS


def test_filled_map_is_reused_for_the_sheet_gate(env, monkeypatch):
    """入选判定读过的集合直接喂给两张表开关，不许再查一遍。"""
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    calls = []
    dao_cls = db_mod.FinancialHistoryDAO
    orig = dao_cls.get_deep_field_dates

    def _count(self, *a, **kw):
        calls.append(a)
        return orig(self, *a, **kw)

    monkeypatch.setattr(dao_cls, "get_deep_field_dates", _count)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    env.collect_historical_financial_data([{"code": "600519"}])
    assert len(calls) == 1, f"同一份集合查了 {len(calls)} 次"


# ── EM 冷却（母方 2026-11-05 裁定，批18-d）─────────────────

def test_upstream_never_has_interest_expense_cools_down_after_first_pull(env, monkeypatch):
    """产线真实形状：INTEREST_EXPENSE 恒 NaN → 拉一次就冷却，不每天白拉。"""
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates, interest_expense=None)
    stocks = [{"code": "600519"}]
    assert env.collect_historical_financial_data(stocks) == {"600519": True}
    assert _ic("600519", dates[0]) is None, "NaN 利息费用不该算出 ic"
    assert _marks("600519") == [{"status": "no_data",
                                 "covered_report_date": dates[0]}]
    cap.clear()
    assert env.collect_historical_financial_data(stocks) == {}, "冷却没生效，第二天又白拉"
    assert cap == {}, f"冷却期内仍发了请求：{cap}"


def test_cooldown_expires_when_a_new_report_period_arrives(env, monkeypatch):
    """冷却只到"新报告期出现"为止：新期一到必须重新入选。"""
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates, interest_expense=None)
    env.collect_historical_financial_data([{"code": "600519"}])
    newer = _recent_dates(1, end_offset=1) + dates
    _seed("600519", newer[:1], ic=False, fcf=False)
    cap.clear()
    _install_ak(monkeypatch, cap, newer, interest_expense=None)
    assert env.collect_historical_financial_data([{"code": "600519"}]) == {"600519": True}
    assert cap["ps"] == [em_f10_symbol("600519")]


def test_request_failure_is_partial_and_retried_next_run(env, monkeypatch):
    """网络失败 ≠ 上游没有：记 partial，明天照样重试。"""
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates, ps_error=True)
    stocks = [{"code": "600519"}]
    env.collect_historical_financial_data(stocks)
    assert _marks("600519")[0]["status"] == "partial"
    cap.clear()
    _install_ak(monkeypatch, cap, dates)
    assert env.collect_historical_financial_data(stocks) == {"600519": True}
    assert _ic("600519", dates[0]) == 9.0


def test_cooldown_does_not_suppress_the_staleness_rule(env, monkeypatch):
    """冷却只挡"深层字段缺"这一条入选理由，报告期过旧照拉。"""
    dates = _recent_dates(3, end_offset=100)
    _seed("600519", dates, ic=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates, interest_expense=None)
    env.collect_historical_financial_data([{"code": "600519"}])
    assert _marks("600519")[0]["status"] == "no_data"
    cap.clear()
    assert env.collect_historical_financial_data([{"code": "600519"}]) == {"600519": True}


def test_force_full_ignores_cooldown(env, monkeypatch):
    """全量重采是修老数据的手段，冷却标记不许挡它。"""
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates, interest_expense=None)
    env.collect_historical_financial_data([{"code": "600519"}])
    cap.clear()
    out = env.collect_historical_financial_data([{"code": "600519"}], force_full=True)
    assert out == {"600519": True}
    assert cap["ps"] == [em_f10_symbol("600519")]


def test_skeleton_empty_leaves_no_cooldown_mark(env, monkeypatch):
    """骨架没出数 = 这次没取到，不是上游没有 → 不标记，明天重试。"""
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates, abs_empty=True)
    env.collect_historical_financial_data([{"code": "600519"}])
    assert _marks("600519") == []
    cap.clear()
    _install_ak(monkeypatch, cap, dates)
    assert env.collect_historical_financial_data([{"code": "600519"}]) == {"600519": True}


def test_filled_fields_mark_done(env, monkeypatch):
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    cap = {}
    _install_ak(monkeypatch, cap, dates)
    env.collect_historical_financial_data([{"code": "600519"}])
    assert _marks("600519") == [{"status": "done",
                                 "covered_report_date": dates[0]}]


@pytest.mark.parametrize("fetch_failed,still_missing,expect", [
    (True, True, "partial"),
    (True, False, "partial"),
    (False, True, "no_data"),
    (False, False, "done"),
])
def test_mark_status_matrix(env, fetch_failed, still_missing, expect):
    """status 三态的判定表：失败优先于缺数，缺数优先于补齐。"""
    from src.collector.akshare_fetcher import _mark_deep_progress, EM_DEEP_FIELDS
    dates = _recent_dates(2)
    recs = [{"stock_code": "600519", "report_date": d} for d in dates]
    filled = {f: set(dates) for f in EM_DEEP_FIELDS}
    if still_missing:
        filled[EM_DEEP_FIELDS[0]] = set(dates[1:])
    else:
        for r in recs:
            r[EM_DEEP_FIELDS[0]] = 9.0
    got = _mark_deep_progress(db_mod.FinancialHistoryDAO(), "600519", recs,
                              filled, fetch_failed)
    assert got == expect
    assert _marks("600519")[0]["status"] == expect


# ── 效果证据要能在日志里读到 ──────────────────────────────

def test_selection_counts_are_logged(env, monkeypatch):
    """没有这行日志，"19-a 多拉了几只、冷却挡了几只"就只能靠推断。"""
    dates = _recent_dates(4)
    _seed("600519", dates, ic=False)
    _seed("000001", dates)
    cap = {}
    _install_ak(monkeypatch, cap, dates, interest_expense=None)
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
    stocks = [{"code": "600519"}, {"code": "000001"}]
    env.collect_historical_financial_data(stocks)
    line = [m for m in logged if "增量入选" in m]
    assert line and "深层字段缺失 1 只" in line[0], f"入选归因日志缺失：{logged}"
    logged.clear()
    env.collect_historical_financial_data(stocks)
    line = [m for m in logged if "增量入选" in m]
    assert line and "冷却跳过 1 只" in line[0], f"冷却计数没进日志：{logged}"


# ── 新 DAO 方法本身的口径 ─────────────────────────────────

def test_get_report_dates_is_newest_first_and_batched(env):
    _seed("600519", ["2024-12-31", "2024-09-30", "2025-03-31"])
    _seed("000001", ["2025-06-30"])
    dao = db_mod.FinancialHistoryDAO()
    out = dao.get_report_dates(["600519", "000001", "920000"])
    assert out["600519"] == ["2025-03-31", "2024-12-31", "2024-09-30"]
    assert out["000001"] == ["2025-06-30"]
    assert "920000" not in out, "库里没这只票就不该出现在结果里"
    assert dao.get_report_dates([]) == {}


def test_get_backfill_marks_returns_status_and_covered_date(env):
    dao = db_mod.FinancialHistoryDAO()
    dao.mark_backfill_done("em_deep_gap", "600519", 3, "no_data",
                           covered_report_date="2026-09-30")
    dao.mark_backfill_done("other_task", "000001", 1, "done",
                           covered_report_date="2026-09-30")
    out = dao.get_backfill_marks("em_deep_gap", ["600519", "000001"])
    assert out == {"600519": {"status": "no_data",
                              "covered_report_date": "2026-09-30"}}, \
        "别的任务的标记不许串进来"
    assert dao.get_backfill_marks("em_deep_gap", []) == {}