"""财务汇总语义回归（2026-10-26，审计 D1；2026-10-28 追加 N1、N20、N21）。

- 5y/10y ROE 任一侧缺失时，roe_improvement 必须为 None，不能拿 0 相减
- 0.0 是有效值（0% ROE / 0% ROIC），不能因 truthiness 被写成 NULL
- 股本稀释率方向必须正确：扩股为正、回购为负（门规 9 依赖此符号）
- 重建范围必须跟随 stocks 入参：钉选 1 只不得重写全市场
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    return db_mod


def _seed(code, roes):
    rows = []
    for i, roe in enumerate(roes):
        rows.append({"stock_code": code,
                     "report_date": f"{2016 + i}-12-31", "roe": roe})
    db_mod.FinancialHistoryDAO().batch_save(rows)


def test_missing_5y_roe_does_not_fake_trend(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    # 2016-2020 有 ROE，2021-2025 缺失 → 5y 缺失、10y=10，趋势必须是 None
    _seed("600519", [10.0] * 5 + [None] * 5)
    rebuild_financial_summaries([{"code": "600519"}])
    s = db_mod.FinancialSummaryDAO().get("600519")
    assert s["roe_5y_avg"] is None
    assert s["roe_10y_avg"] == 10.0
    assert s["roe_improvement"] is None


def test_zero_roe_is_preserved_not_null(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed("000001", [10.0] * 5 + [0.0] * 5)
    rebuild_financial_summaries([{"code": "000001"}])
    s = db_mod.FinancialSummaryDAO().get("000001")
    assert s["roe_5y_avg"] == 0.0
    assert s["roe_10y_avg"] == 5.0
    assert s["roe_improvement"] == -5.0


# ── 股本稀释率方向（2026-10-28，审计 N1）────────────────────────────────
# reports 按 report_date 升序，故 5y 窗口 = 最近 5 份年报，[-1] 是最新。
# 旧实现把 earliest/latest 取反：扩股算出负数（门规 9 放行）、回购算出正数（被误杀）。

def _seed_field(code, field, values):
    rows = [{"stock_code": code, "report_date": f"{2016 + i}-12-31", field: v}
            for i, v in enumerate(values)]
    db_mod.FinancialHistoryDAO().batch_save(rows)


def _seed_shares(code, shares_by_year):
    _seed_field(code, "total_shares", [float(s) for s in shares_by_year])


def test_share_issuance_is_positive_dilution(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("600519", [100] * 9 + [200])   # 2025 年翻倍扩股
    rebuild_financial_summaries([{"code": "600519"}])
    s = db_mod.FinancialSummaryDAO().get("600519")
    assert s["share_dilution_5y"] == 100.0
    assert s["share_dilution_10y"] == 100.0


def test_buyback_is_negative_dilution(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("000858", [200] * 9 + [100])   # 2025 年回购注销一半
    rebuild_financial_summaries([{"code": "000858"}])
    s = db_mod.FinancialSummaryDAO().get("000858")
    assert s["share_dilution_5y"] == -50.0
    assert s["share_dilution_10y"] == -50.0


def test_dilution_5y_window_is_recent_years_only(db):
    """扩股只发生在 2017：5y 窗口应为 0（不含早年），10y 窗口应看到 +100。"""
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("600036", [100, 200] + [200] * 8)
    rebuild_financial_summaries([{"code": "600036"}])
    s = db_mod.FinancialSummaryDAO().get("600036")
    assert s["share_dilution_5y"] == 0.0
    assert s["share_dilution_10y"] == 100.0


# ── 重建范围（2026-10-28，审计 N20）────────────────────────────────────
# 旧实现收下 stocks 却从不使用：钉选 1 只也会重写全市场 summary，
# 且跑在 onboard 的请求线程里，与后台回填抢写锁。

def test_rebuild_only_touches_requested_codes(db):
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("600519", [100] * 9 + [200])
    _seed_shares("000001", [100] * 9 + [200])
    rebuild_financial_summaries([{"code": "600519"}])
    assert db_mod.FinancialSummaryDAO().get("600519") is not None
    assert db_mod.FinancialSummaryDAO().get("000001") is None, \
        "未请求的代码不得被写入（旧实现会连它一起重建）"


def test_rebuild_none_rebuilds_all(db):
    """stocks=None 是显式全量入口，行为与旧版一致。"""
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("600519", [100] * 9 + [200])
    _seed_shares("000001", [100] * 9 + [200])
    rebuild_financial_summaries(None)
    assert db_mod.FinancialSummaryDAO().get("600519") is not None
    assert db_mod.FinancialSummaryDAO().get("000001") is not None


def test_rebuild_empty_list_is_noop(db):
    """空列表＝无待重建代码，既不写库也不得退化成全量。"""
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("600519", [100] * 9 + [200])
    rebuild_financial_summaries([])
    assert db_mod.FinancialSummaryDAO().get("600519") is None


def test_scoped_rebuild_matches_full_rebuild(db):
    """缩小扫描范围不能改变计算结果。"""
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_shares("600519", [100, 120, 140, 160, 180, 200, 220, 240, 260, 280])
    _seed_shares("000001", [100] * 9 + [200])
    rebuild_financial_summaries(None)
    full = db_mod.FinancialSummaryDAO().get("600519")
    with db_mod.db_conn() as conn:   # FinancialSummaryDAO 无 delete，直接清行
        conn.execute("DELETE FROM financial_summary WHERE stock_code = ?",
                     ("600519",))
    assert db_mod.FinancialSummaryDAO().get("600519") is None
    rebuild_financial_summaries([{"code": "600519"}])
    scoped = db_mod.FinancialSummaryDAO().get("600519")
    for k in ("roe_5y_avg", "roe_10y_avg", "share_dilution_5y",
              "share_dilution_10y", "data_years"):
        assert scoped[k] == full[k], f"{k} 口径漂移：{scoped[k]} != {full[k]}"


def test_get_annual_by_codes_crosses_chunk_boundary(db):
    """新 SQL 用 IN(...) 分块（450/块，SQLite 变量上限 999）——边界必须取全。"""
    codes = [f"6{i:05d}" for i in range(451)]   # 450 + 1，逼出第二块
    rows = [{"stock_code": c, "report_date": "2020-12-31", "roe": 10.0}
            for c in codes]
    db_mod.FinancialHistoryDAO().batch_save(rows)
    got = db_mod.FinancialHistoryDAO().get_annual_by_codes(codes)
    assert len(got) == 451, f"分块边界丢行：{len(got)}"
    assert {r["stock_code"] for r in got} == set(codes)
    # 未请求的代码不得混进来
    assert db_mod.FinancialHistoryDAO().get_annual_by_codes(
        [codes[0]]) [0]["stock_code"] == codes[0]


def test_zero_roic_is_preserved_not_null(db):
    """回归 N21：10 年均 ROIC 恰为 0 是合法测量值。

    旧写法 `if _avg(roic_vals_10)` 走 truthiness，0.0 被判成"没有"写 NULL，
    与本文件 test_zero_roe_is_preserved_not_null 主张的不变量（0 是有效值）冲突；
    5y 那行用的是 `is not None`，10y 是同族的漏修。
    """
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_field("601988", "roic", [0.0] * 10)
    rebuild_financial_summaries([{"code": "601988"}])
    s = db_mod.FinancialSummaryDAO().get("601988")
    assert s["roic_10y_avg"] == 0.0
    assert s["roic_5y_avg"] == 0.0


# ── OCF/NI 精确门数据（2026-10-29，审计 N5①）─────────────────────────────
# 同窗配对：只取同时有 net_operate_cashflow 与 net_profit 的年份；共同年份
# <3 不产出（宁缺勿错：分子分母必须同一报告期窗口，比值才无量纲）。

def _seed_ocf_np(code, ocf_by_year, np_by_year):
    rows = []
    for i, (o, n) in enumerate(zip(ocf_by_year, np_by_year)):
        rows.append({"stock_code": code, "report_date": f"{2021 + i}-12-31",
                     "net_operate_cashflow": o, "net_profit": n})
    db_mod.FinancialHistoryDAO().batch_save(rows)


def test_ocf_ni_paired_sums_populated(db):
    """3+ 共同年份 → 两侧同窗累计成对产出（缺一侧的年份剔除，不单独求和）。"""
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_ocf_np("600000",
                 [100.0, 200.0, 300.0, 400.0, None],
                 [50.0, 80.0, 100.0, 150.0, 999.0])   # 2025 缺 OCF → 剔除
    rebuild_financial_summaries([{"code": "600000"}])
    s = db_mod.FinancialSummaryDAO().get("600000")
    assert s["ocf_5y_sum"] == 1000.0            # 100+200+300+400
    assert s["net_profit_5y_sum"] == 380.0      # 50+80+100+150（999 不入窗）


def test_ocf_ni_insufficient_common_years_is_null(db):
    """共同年份 <3 → 两侧同时 NULL（不拿残缺窗口冒充 5 年累计）。"""
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    _seed_ocf_np("600001",
                 [100.0, None, None, None, 500.0],
                 [50.0, 80.0, None, 150.0, 200.0])    # 共同年份仅 2021/2025
    rebuild_financial_summaries([{"code": "600001"}])
    s = db_mod.FinancialSummaryDAO().get("600001")
    assert s["ocf_5y_sum"] is None
    assert s["net_profit_5y_sum"] is None
