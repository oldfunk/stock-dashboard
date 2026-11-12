"""``get_db_path`` 的 ``STOCK_DASHBOARD_DB`` 覆盖（2026-11-12）。

研究线/测试要跑在**合成库**上时，不必等真实采集，也不许碰生产库。
本用例只验路径解析，不建库（建库由 ``make_synthetic_env.py`` 自己负责）。
"""

import src.models.database as db_mod


def test_env_override_points_to_synthetic_db(tmp_path, monkeypatch):
    target = tmp_path / "stock_dashboard_synthetic.db"
    monkeypatch.setenv("STOCK_DASHBOARD_DB", str(target))
    assert db_mod.get_db_path() == str(target)


def test_env_override_supports_relative_path(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("STOCK_DASHBOARD_DB", "data/db/syn.db")
    assert db_mod.get_db_path() == str(Path("data/db/syn.db"))


def test_default_path_when_env_absent(monkeypatch):
    monkeypatch.delenv("STOCK_DASHBOARD_DB", raising=False)
    assert db_mod.get_db_path().replace("\\", "/").endswith(
        "data/db/stock_dashboard.db")


def test_env_not_set_does_not_create_synthetic(tmp_path, monkeypatch):
    """设了环境变量才建目录；没设时不得因合成路径去 mkdir。"""
    monkeypatch.setenv("STOCK_DASHBOARD_DB", str(tmp_path / "nested" / "syn.db"))
    db_mod.get_db_path()
    assert (tmp_path / "nested").is_dir()
