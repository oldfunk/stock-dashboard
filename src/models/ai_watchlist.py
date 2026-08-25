"""AI 观察池 + 投资笔记 DAO 层

独立文件避免 database.py 继续膨胀。三张表对应三个 DAO：
- AiWatchlistDAO: 当前观察池状态（固定 5 行，由 AI 保证容量）
- AiWatchlistHistoryDAO: 历史调整记录（add/remove/keep 都记）
- AiJournalDAO: 投资笔记 CRUD
"""

from typing import Optional

from src.models.database import db_conn
from src.utils import now_cn


class AiWatchlistDAO:
    """当前观察池状态（固定 5 只，由 WatchlistReviewer 保证容量）"""

    def get_all(self) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_watchlist ORDER BY added_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_code(self, code: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_watchlist WHERE code = ?", (code,)
            ).fetchone()
        return dict(row) if row else None

    def add(self, code: str, name: str, added_reason: str = None,
            ai_confidence: str = None) -> bool:
        with db_conn() as conn:
            cur = conn.execute(
                "INSERT OR REPLACE INTO ai_watchlist "
                "(code, name, added_at, added_reason, ai_confidence, last_reviewed, review_count) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)",
                (code, name, now_cn().isoformat(), added_reason, ai_confidence,
                 now_cn().strftime("%Y-%m-%d"))
            )
            return cur.rowcount > 0

    def remove(self, code: str) -> bool:
        with db_conn() as conn:
            cur = conn.execute("DELETE FROM ai_watchlist WHERE code = ?", (code,))
            return cur.rowcount > 0

    def update_reviewed(self, code: str, confidence: str = None):
        """复盘后更新置信度 + 在池周数 + 最后复盘日期"""
        with db_conn() as conn:
            conn.execute(
                "UPDATE ai_watchlist SET last_reviewed = ?, review_count = review_count + 1"
                + (", ai_confidence = ?" if confidence else "")
                + " WHERE code = ?",
                ((now_cn().strftime("%Y-%m-%d"), confidence, code)
                 if confidence else (now_cn().strftime("%Y-%m-%d"), code))
            )

    def clear(self):
        """清空观察池（首次初始化或重置时用）"""
        with db_conn() as conn:
            conn.execute("DELETE FROM ai_watchlist")


class AiWatchlistHistoryDAO:
    """历史调整记录（add/remove/keep 都追加一条）"""

    def append(self, code: str, name: str, action: str, reason: str,
               review_run_id: str, action_date: str = None):
        if action_date is None:
            action_date = now_cn().strftime("%Y-%m-%d")
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO ai_watchlist_history "
                "(code, name, action, action_date, reason, review_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (code, name, action, action_date, reason, review_run_id)
            )

    def list_by_code(self, code: str, limit: int = 50) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_watchlist_history WHERE code = ? "
                "ORDER BY action_date DESC, id DESC LIMIT ?",
                (code, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_by_date(self, action_date: str) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_watchlist_history WHERE action_date = ? "
                "ORDER BY id ASC",
                (action_date,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_recent(self, limit: int = 20) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_watchlist_history "
                "ORDER BY action_date DESC, id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


class AiJournalDAO:
    """投资笔记 CRUD"""

    def save(self, journal_date: str, run_id: str, title: str,
             content_md: str, market_snapshot: str = None,
             actions_summary: str = None) -> int:
        now = now_cn().isoformat()
        with db_conn() as conn:
            cur = conn.execute(
                "INSERT OR REPLACE INTO ai_journal "
                "(journal_date, run_id, title, content_md, market_snapshot, "
                "actions_summary, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (journal_date, run_id, title, content_md,
                 market_snapshot, actions_summary, now)
            )
            return cur.lastrowid

    def get_latest(self) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_journal ORDER BY journal_date DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_by_date(self, journal_date: str) -> Optional[dict]:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_journal WHERE journal_date = ?",
                (journal_date,)
            ).fetchone()
        return dict(row) if row else None

    def list_all(self, limit: int = 50) -> list[dict]:
        """轻量列表（仅 date + title，用于 sidebar）"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT id, journal_date, title FROM ai_journal "
                "ORDER BY journal_date DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_previous(self, journal_date: str) -> Optional[dict]:
        """获取指定日期前一篇笔记"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_journal WHERE journal_date < ? "
                "ORDER BY journal_date DESC LIMIT 1",
                (journal_date,)
            ).fetchone()
        return dict(row) if row else None

    def get_next(self, journal_date: str) -> Optional[dict]:
        """获取指定日期后一篇笔记"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_journal WHERE journal_date > ? "
                "ORDER BY journal_date ASC LIMIT 1",
                (journal_date,)
            ).fetchone()
        return dict(row) if row else None
