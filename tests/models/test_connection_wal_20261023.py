"""数据库连接 WAL / 锁竞争回归测试（2026-10-23）。

背景：``get_connection()`` 曾在每次连接时执行 ``PRAGMA journal_mode=WAL``（需写锁），
调度器写库期间读连接（如首页轮询 ``/api/indices``）会抛 ``database is locked``。
现改为 ``busy_timeout=5000`` 等待锁 + 仅在非 WAL 时才设置（读连接不再抢写锁）。
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
    """连接必须设置 busy_timeout，且 WAL 生效"""
    from src.models import database as db_mod
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
