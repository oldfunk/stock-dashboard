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

    def test_re_add_preserves_monitor_condition(self, db):
        """回归 N19：重纳不得抹掉已设的监控条件。

        上面所有用例都是「先 add 再设条件」，从未覆盖「设完条件后再 add」，
        而旧实现用 INSERT OR REPLACE（先删行再插），列清单里没有
        monitor_condition → 重钉一只票就静默清空用户/AI 设的监控条件。
        """
        dao = AiWatchlistDAO()
        dao.add('600519', '贵州茅台')
        cond = json.dumps({'metric': 'pe', 'operator': 'lt', 'threshold': 10})
        dao.update_monitor_condition('600519', cond)
        assert dao.add('600519', '贵州茅台', added_reason='二次调入') is True
        assert dao.get_monitor_condition('600519') == cond

    def test_re_add_still_resets_declared_fields(self, db):
        """add() 声明要重置的字段必须真重置——防止改成 upsert 时把语义一并改掉。"""
        dao = AiWatchlistDAO()
        dao.add('600519', '贵州茅台')
        dao.remove('600519', reason='高估')
        dao.set_status('600519', 'watch', reason='回调观察',
                       watch_until='2026-12-31')
        cond = json.dumps({'metric': 'roe', 'operator': 'gt', 'threshold': 15})
        dao.update_monitor_condition('600519', cond)

        assert dao.add('600519', '贵州茅台', added_reason='重新发现') is True
        row = dao.get_by_code('600519')
        assert row['status'] == 'core'
        assert row['status_reason'] is None
        assert row['watch_until'] is None
        assert row['review_count'] == 1
        assert row['added_reason'] == '重新发现'
        assert row['monitor_condition'] == cond, '监控条件不在重置清单内'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
