"""批18-c：研究域日期比较统一 SUBSTR（quality.py），产线接口保留索引（data_api.py）。

母方批18 裁定：「SUBSTR 统一：接受（研究域正确性优先；780 行表扫描便宜）。
data_api.py 保持索引（产线翻页）+ 加"仅支持 YYYY-MM-DD"注释；quality.py 跟研究域统一。」

为什么这不是空改：``analysis_date`` 是 TEXT，裸 ``<= '2026-07-08'`` 在字符串比较下
会把 ``'2026-07-08T15:00:00'`` 判为**大于**边界，同日行整批漏掉——覆盖率与异常值
计数随之偏低，质量门却照样判 pass。产线当前 782/782 行都是 10 位纯日期（只读实测），
所以这是**防御性统一**，不是在修一个正在错的东西；用例用带时间分量的行把这条
防御钉成可证伪的，否则改动无法被变异测试抓住。

本文件日期写死是有意的：``assess_*`` 只做窗口计数，不做新鲜度判定
（陈旧度在 ``evaluate`` 里，那边才需要相对今天，见 test_quality.py 的 TODAY 注释）。
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "q18c.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _outcome(analysis_id, analysis_date, ret_20=6.0, excess_20=5.0):
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT INTO analysis_outcome (analysis_id, benchmark_code, stock_code, "
            "analysis_date, entry_date, ret_20, excess_20, updated_at) "
            "VALUES (?, 'sh000300', '600519', ?, '2026-07-09', ?, ?, '2026-10-01')",
            (analysis_id, analysis_date, ret_20, excess_20))


def test_same_day_row_with_time_component_is_counted(db):
    """同日带时间分量的行必须算进窗口——裸 <= 会把它整行漏掉。"""
    from src.research.quality import assess_analysis_outcome
    _outcome(1, "2026-07-08T15:00:00")
    stats = assess_analysis_outcome("2026-07-08")
    assert stats["details"]["rows"] == 1, "同日行被日期边界漏掉了"
    assert stats["coverage"] == 1.0


def test_time_component_rows_count_into_coverage_and_outliers(db):
    """三条查询（total / filled / outliers）都得统一，漏一条就口径不一致。

    异常值那行必须放在**边界同日**且带时间分量：字符串比较下
    ``'2026-07-07T10:00:00' <= '2026-07-08'`` 仍为真（第 9 位 7 < 8），
    只有同日带时间才会被裸 <= 漏掉。放前一日的话，只退回 outliers 查询
    这条用例仍绿，那条变异就抓不住（实测踩过，M3 漏抓后改成同日）。
    另留一行前一日带时间分量，钉住 SUBSTR 不会把更早的行过度排除。
    """
    from src.research.quality import assess_analysis_outcome
    _outcome(1, "2026-07-08T15:00:00", ret_20=6.0, excess_20=5.0)      # 同日，已填
    _outcome(2, "2026-07-08T09:30:00", ret_20=None, excess_20=None)    # 同日，未填
    _outcome(3, "2026-07-08T10:00:00", ret_20=400.0, excess_20=400.0)  # 同日，异常值
    _outcome(4, "2026-07-07T10:00:00", ret_20=3.0, excess_20=2.0)      # 前一日，已填
    _outcome(5, "2026-07-09", ret_20=1.0, excess_20=1.0)               # 窗口外
    stats = assess_analysis_outcome("2026-07-08")
    assert stats["details"]["rows"] == 4
    assert stats["details"]["filled"] == 3
    assert stats["coverage"] == 0.75
    assert stats["outliers"] == 1


def test_pure_date_window_semantics_unchanged(db):
    """回归：纯日期行的累积窗口语义一字不变（<= 含当日、含更早、不含更晚）。"""
    from src.research.quality import assess_analysis_outcome
    _outcome(1, "2026-07-07")
    _outcome(2, "2026-07-08")
    _outcome(3, "2026-07-09")
    stats = assess_analysis_outcome("2026-07-08")
    assert stats["details"]["rows"] == 2
    assert stats["coverage"] == 1.0
    assert assess_analysis_outcome("2026-07-06")["details"]["rows"] == 0


def test_empty_table_still_reports_zero_coverage(db):
    """空表分支不受影响：total=0 时仍返回 coverage 0 / missing_rate 1。"""
    from src.research.quality import assess_analysis_outcome
    stats = assess_analysis_outcome("2026-07-08")
    assert stats["coverage"] == 0.0 and stats["missing_rate"] == 1.0
    assert stats["details"]["rows"] == 0


def test_kline_daily_assessment_is_single_day_not_cumulative(db):
    """kline_daily 的 trade_date = ? 故意不套 SUBSTR：单日语义 + 走索引。

    这条钉的是「为什么那 4 处不动」：kline_daily.trade_date 是纯日期
    （产线 5,307 个不同值全 10 位），等值比较正确且能走 idx_kline_trade_date
    （产线 1,526 万行，套函数就是 4 趟千万行全扫）。
    若有人"顺手统一"成 SUBSTR 累积窗口，跨日行会被算进来，这条会红。

    必须用**两只不同代码**分落两日：total 是 COUNT(DISTINCT code)，
    同一只票落两日的话累积窗口算出来还是 1，变异抓不住（实测踩过，M4 漏抓）。
    """
    from src.research.quality import assess_kline_daily
    dao = db_mod.KlineDAO()
    bar = {"open": 1.0, "close": 1.0, "high": 1.0, "low": 1.0,
           "volume": 1.0, "amount": 1.0, "turnover": 0.1,
           "close_raw": 1.0, "close_adj": 1.0}
    dao.upsert_many("600519", [dict(bar, trade_date="2026-07-08")])
    dao.upsert_many("000001", [dict(bar, trade_date="2026-07-07")])
    stats = assess_kline_daily("2026-07-08")
    assert stats["details"]["codes"] == 1, "单日评估不得把更早日期的票算进来"
    assert stats["coverage"] == 1.0