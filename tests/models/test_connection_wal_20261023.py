"""数据库连接 WAL / 锁竞争回归测试（2026-10-23，2026-10-26 更新）。

背景：``get_connection()`` 曾在每次连接时执行 ``PRAGMA journal_mode=WAL``（需写锁），
调度器写库期间读连接会抛 ``database is locked``。2026-10-26 进一步发现：即使只
查询 ``PRAGMA journal_mode`` 也会抢写锁，且不受 busy_timeout 保护——全市场回填
在 145 只处被此打断。现在 WAL 只在 ``init_database()`` 设置一次，连接只设
busy_timeout / foreign_keys。
"""

import sqlite3

import pytest


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    from src.models import database as db_mod
    p = str(tmp_path / "conn.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: p)
    return p


def test_connection_sets_busy_timeout_and_wal(db_path):
    """初始化后 WAL 生效；连接只设 busy_timeout"""
    from src.models import database as db_mod
    db_mod.init_database()
    conn = db_mod.get_connection()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_read_connection_ok_while_writer_holds_lock(db_path):
    """写事务持有期间，读连接仍可建立并读取（WAL 下读写不互斥）。"""
    from src.models import database as db_mod
    writer = sqlite3.connect(db_path, timeout=5.0)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE t (x INTEGER)")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO t VALUES (1)")
    try:
        conn = db_mod.get_connection()
        try:
            # 未提交，读侧看不到该行（0）
            assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        writer.rollback()
        writer.close()
