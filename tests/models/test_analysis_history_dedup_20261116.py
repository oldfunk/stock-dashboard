"""stock_analysis_history 同 (股票, 批次) 去重（2026-11-16）。

实录：AI 触发恢复时先跑了一次"未配置 Key"的失败标记（20 条 model=NULL），
修复 .env 后重跑成功行——纯 INSERT 让同一批次积了 40 条。现在同
(code, run_id) 覆盖，跨 run_id 的历史累积不受影响。
"""

import json

from src.models import database as db_mod


def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "h.db"))
    db_mod.init_database()
    return db_mod.StockAnalysisHistoryDAO()


def test_same_run_replaces_not_duplicates(tmp_path, monkeypatch):
    dao = _tmp(tmp_path, monkeypatch)
    dao.save("000792", "run1", 90.0, json.dumps({"a": 1}), "{}", "glm-4.5-flash")
    dao.save("000792", "run1", 80.0, json.dumps({"a": 2}), "{}", "glm-4.5-flash")
    with db_mod.db_conn() as conn:
        rows = conn.execute(
            "SELECT ai_analysis FROM stock_analysis_history "
            "WHERE stock_code='000792'").fetchall()
    assert len(rows) == 1, "同一批次重跑只留一条"
    assert json.loads(rows[0][0]) == {"a": 2}


def test_different_runs_accumulate(tmp_path, monkeypatch):
    dao = _tmp(tmp_path, monkeypatch)
    dao.save("000792", "run1", 90.0, "{}", "{}")
    dao.save("000792", "run2", 80.0, "{}", "{}")
    with db_mod.db_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM stock_analysis_history "
            "WHERE stock_code='000792'").fetchone()[0]
    assert n == 2, "跨批次历史应累积"
