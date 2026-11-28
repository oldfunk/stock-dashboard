"""NewsDAO.between：日期型发布时间不应漏进前一日窗口。"""
from __future__ import annotations

import sqlite3

from src.models.news import NewsDAO


def test_between_excludes_next_day_date_only():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE news_item (id INTEGER PRIMARY KEY, code TEXT, "
                 "name TEXT, title TEXT, summary TEXT, url TEXT, source TEXT, "
                 "published_at TEXT, fetched_at TEXT, sentiment TEXT, "
                 "ai_brief TEXT, summarized_at TEXT)")
    conn.executemany(
        "INSERT INTO news_item (id, title, published_at, fetched_at) "
        "VALUES (?,?,?,?)", [
            (1, "当天带时间", "2026-11-27 10:00:00", "2026-11-27 10:01:00"),
            (2, "当天只有日期", "2026-11-27", "2026-11-27 10:02:00"),
            (3, "次日只有日期", "2026-11-28", "2026-11-27 10:03:00"),
        ])
    dao = NewsDAO(conn_factory=lambda: conn)
    rows = dao.between("2026-11-27 00:00:00", "2026-11-28 00:00:00")
    assert [r["id"] for r in rows] == [1, 2]
    conn.close()
