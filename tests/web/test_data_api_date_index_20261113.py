"""批18-c：产线翻页接口保留索引区间 SEARCH，代价是只支持 YYYY-MM-DD。

母方批18 裁定：「data_api.py 保持索引（产线翻页）+ 加"仅支持 YYYY-MM-DD"注释」。
注释本身是不可证伪的承诺，所以这里把它钉成两条可执行断言：

1. 端点真正构造出的 SQL 必须走 ``idx_analysis_outcome_date`` 的 **SEARCH**（区间），
   不是 SCAN。用 spy 捕获端点实际 SQL 再 EXPLAIN，而不是在测试里重抄一份 SQL——
   重抄的话端点改了测试还绿，等于没钉。
2. 纯日期区间过滤行为正确；带时间分量的同日行会被漏掉，这是**已声明的代价**
   （研究域 quality.py 那边已统一 SUBSTR）。把它写成用例，将来谁改写入格式，
   这条会红，逼一次显式裁定，而不是静默漏数据。

SQLite 实测（本机，见交付贴）：
   裸 ``analysis_date <= ?``        -> SEARCH ... USING COVERING INDEX (analysis_date<?)
   ``SUBSTR(analysis_date,1,10) <= ?`` -> SCAN ... USING COVERING INDEX
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    db_path = str(tmp_path / "test18c.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def _outcome(analysis_id, analysis_date):
    from src.models import database as db_mod
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT INTO analysis_outcome (analysis_id, benchmark_code, stock_code, "
            "analysis_date, entry_date, signal, ret_20, excess_20, updated_at) "
            "VALUES (?, 'sh000300', '600519', ?, '2026-07-09', 'BUY', 6.0, 5.0, "
            "'2026-10-01')", (analysis_id, analysis_date))


def _capture_outcomes_sql(client, monkeypatch, params):
    """调一次 /outcomes，捕获端点真正执行的 SQL 与参数。"""
    from src.web import data_api
    seen = {}
    real = data_api._rows

    def spy(sql, params=()):
        seen["sql"] = sql
        seen["params"] = list(params)
        return real(sql, params)

    monkeypatch.setattr(data_api, "_rows", spy)
    r = client.get("/api/data/outcomes", params=params)
    assert r.status_code == 200
    return seen, r.json()["items"]


def test_outcomes_date_filter_keeps_index_range_search(client, monkeypatch):
    from src.models import database as db_mod
    seen, _ = _capture_outcomes_sql(
        client, monkeypatch, {"start": "2026-07-01", "end": "2026-07-08"})
    with db_mod.db_conn() as conn:
        plan = [row[3] for row in conn.execute(
            "EXPLAIN QUERY PLAN " + seen["sql"], seen["params"])]
    assert any("SEARCH" in p and "idx_analysis_outcome_date" in p for p in plan), plan
    assert not any(p.startswith("SCAN") for p in plan), (
        f"日期过滤退化成全扫，产线翻页会慢：{plan}")


def test_outcomes_pure_date_range_is_inclusive(client):
    _outcome(1, "2026-06-30")   # 早于 start
    _outcome(2, "2026-07-01")   # 恰在 start
    _outcome(3, "2026-07-08")   # 恰在 end
    _outcome(4, "2026-07-09")   # 晚于 end
    items = client.get("/api/data/outcomes",
                       params={"start": "2026-07-01", "end": "2026-07-08"}).json()["items"]
    assert {r["analysis_date"] for r in items} == {"2026-07-01", "2026-07-08"}


def test_outcomes_declared_cost_time_component_same_day_is_dropped(client):
    """已声明的代价：带时间分量的同日行被字符串比较漏掉（仅支持 YYYY-MM-DD）。

    这不是认可该行为，是把裁定的边界写成可执行记录。产线写入侧目前全为
    10 位纯日期（782/782），所以今天没有数据损失；哪天写入格式变了，这条会红，
    必须显式决定是改写入侧还是放弃这里的索引——不许静默漏。
    """
    _outcome(1, "2026-07-08")
    _outcome(2, "2026-07-08T15:00:00")
    items = client.get("/api/data/outcomes",
                       params={"end": "2026-07-08"}).json()["items"]
    assert {r["analysis_date"] for r in items} == {"2026-07-08"}