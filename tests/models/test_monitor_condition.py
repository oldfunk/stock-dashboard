"""M3-2a monitor_condition 字段 + DAO 方法单测。"""

import json
import sqlite3

import pytest

import src.models.database as db_mod
from src.models.database import init_database
from src.models.ai_watchlist import AiWatchlistDAO


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test.db')
    monkeypatch.setattr(db_mod, 'get_db_path', lambda: db_path)
    init_database()
    return db_path


class TestMonitorCondition:
    def test_column_exists(self, db):
        cols = {r[1] for r in sqlite3.connect(db).execute(
            'PRAGMA table_info(ai_watchlist)').fetchall()}
        assert 'monitor_condition' in cols

    def test_add_and_get(self, db):
        dao = AiWatchlistDAO()
        dao.add('600519', '贵州茅台', added_reason='低PE 高ROE')
        cond = json.dumps({'metric': 'pe', 'operator': 'lt', 'threshold': 10})
        assert dao.update_monitor_condition('600519', cond) is True
        assert dao.get_monitor_condition('600519') == cond

    def test_get_nonexistent(self, db):
        dao = AiWatchlistDAO()
        assert dao.get_monitor_condition('999999') is None

    def test_clear_condition(self, db):
        dao = AiWatchlistDAO()
        dao.add('600519', '贵州茅台')
        cond = json.dumps({'metric': 'pe', 'operator': 'lt', 'threshold': 10})
        dao.update_monitor_condition('600519', cond)
        assert dao.get_monitor_condition('600519') == cond
        dao.update_monitor_condition('600519', None)
        assert dao.get_monitor_condition('600519') is None

    def test_update_nonexistent_code(self, db):
        dao = AiWatchlistDAO()
        assert dao.update_monitor_condition('999999', '{}') is False

    def test_condition_json_roundtrip(self, db):
        dao = AiWatchlistDAO()
        dao.add('600519', '贵州茅台')
        cond = {'metric': 'roe', 'operator': 'gt', 'threshold': 15}
        dao.update_monitor_condition('600519', json.dumps(cond))
        result = json.loads(dao.get_monitor_condition('600519'))
        assert result == cond


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
