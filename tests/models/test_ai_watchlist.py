"""AI 观察池 DAO 单测

使用独立临时数据库，避免污染开发库。
"""

import os
import tempfile
import pytest

from src.models import database as db_mod


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    """每个测试用独立临时数据库"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: path)
    db_mod.init_database()
    yield
    os.unlink(path)


def test_watchlist_add_get_all():
    from src.models.ai_watchlist import AiWatchlistDAO
    dao = AiWatchlistDAO()
    dao.add("600519", "贵州茅台", "ROE 持续 30%+", "高")
    dao.add("000792", "盐湖股份", "低估值高分红", "中")

    all_items = dao.get_all()
    assert len(all_items) == 2
    codes = {item['code'] for item in all_items}
    assert codes == {"600519", "000792"}

    moutai = dao.get_by_code("600519")
    assert moutai is not None
    assert moutai['name'] == "贵州茅台"
    assert moutai['ai_confidence'] == "高"
    assert moutai['review_count'] == 1


def test_watchlist_remove():
    from src.models.ai_watchlist import AiWatchlistDAO
    dao = AiWatchlistDAO()
    dao.add("600519", "贵州茅台", "测试")
    assert dao.remove("600519") is True
    assert dao.get_by_code("600519") is None
    assert dao.remove("999999") is False


def test_watchlist_update_reviewed():
    from src.models.ai_watchlist import AiWatchlistDAO
    dao = AiWatchlistDAO()
    dao.add("600519", "贵州茅台", ai_confidence="中")
    dao.update_reviewed("600519", confidence="高")

    moutai = dao.get_by_code("600519")
    assert moutai['ai_confidence'] == "高"
    assert moutai['review_count'] == 2


def test_watchlist_clear():
    from src.models.ai_watchlist import AiWatchlistDAO
    dao = AiWatchlistDAO()
    dao.add("600519", "贵州茅台", "测试")
    dao.add("000792", "盐湖股份", "测试")
    dao.clear()
    assert dao.get_all() == []


def test_history_append_and_list():
    from src.models.ai_watchlist import AiWatchlistHistoryDAO
    dao = AiWatchlistHistoryDAO()
    dao.append("600519", "贵州茅台", "keep", "基本面稳定", "run_001", "2026-07-20")
    dao.append("000792", "盐湖股份", "add", "新进候选池", "run_001", "2026-07-20")
    dao.append("002415", "海康威视", "remove", "Signal 转 AVOID", "run_001", "2026-07-20")

    by_date = dao.list_by_date("2026-07-20")
    assert len(by_date) == 3

    by_code = dao.list_by_code("600519")
    assert len(by_code) == 1
    assert by_code[0]['action'] == "keep"

    recent = dao.list_recent(limit=2)
    assert len(recent) == 2


def test_journal_save_and_get():
    from src.models.ai_watchlist import AiJournalDAO
    dao = AiJournalDAO()
    journal_id = dao.save(
        journal_date="2026-07-20",
        run_id="run_001",
        title="本周复盘 - 市场震荡",
        content_md="# 本周复盘\n市场震荡，观察池稳定",
        market_snapshot='{"sh": 3174}',
        actions_summary='{"add": 1, "remove": 1}'
    )
    assert journal_id > 0

    latest = dao.get_latest()
    assert latest is not None
    assert latest['title'] == "本周复盘 - 市场震荡"
    assert latest['journal_date'] == "2026-07-20"

    by_date = dao.get_by_date("2026-07-20")
    assert by_date is not None
    assert by_date['run_id'] == "run_001"

    # 不存在的日期
    assert dao.get_by_date("2020-01-01") is None


def test_journal_list_all():
    from src.models.ai_watchlist import AiJournalDAO
    dao = AiJournalDAO()
    for i in range(5):
        dao.save(
            journal_date=f"2026-07-{i+1:02d}",
            run_id=f"run_{i}",
            title=f"第{i}周",
            content_md="..."
        )
    items = dao.list_all()
    assert len(items) == 5
    # 最新在前
    assert items[0]['journal_date'] == "2026-07-05"
    # list_all 只返回轻量字段
    assert 'content_md' not in items[0]
    assert 'title' in items[0]
