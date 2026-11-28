"""收盘检阅 / 重大事件的持久层（F 线，2026-11-28）。

笔记仍写 `ai_journal`（设计 §5：不新建第二套笔记系统），用 `note_type`
区分 `weekly`（周六复盘）与 `close_review`（每日收盘检阅）。`status` 只有
`published`/`annotated`，人工批注不阻塞、不覆盖 AI 原文。
"""
from __future__ import annotations

import json
from typing import Optional

from src.utils import now_cn


class NewsReviewDAO:
    def __init__(self, conn_factory=None):
        if conn_factory is None:
            from src.models.database import db_conn as conn_factory
        self._conn = conn_factory

    # ── 笔记 ──────────────────────────────────────────────
    def save_review(self, journal_date: str, title: str, content_md: str,
                    sources: list[dict], market_snapshot: dict,
                    actions_summary: dict, run_id: str,
                    status: str = 'published', model: str = '') -> dict:
        """幂等写入当日检阅；`annotated` 的人工版本不被 AI 覆盖。"""
        now = now_cn().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, status FROM ai_journal WHERE journal_date = ?",
                (journal_date,)).fetchone()
            if row and (row['status'] or '') == 'annotated':
                return {'saved': False, 'reason': 'annotated',
                        'journal_date': journal_date}
            args = (run_id, title, content_md,
                    json.dumps(market_snapshot, ensure_ascii=False),
                    json.dumps(actions_summary, ensure_ascii=False),
                    json.dumps(sources, ensure_ascii=False),
                    status, now)
            if row:
                conn.execute(
                    "UPDATE ai_journal SET run_id=?, title=?, content_md=?, "
                    "market_snapshot=?, actions_summary=?, sources_json=?, "
                    "note_type='close_review', status=?, updated_at=? "
                    "WHERE journal_date=?", args + (journal_date,))
            else:
                conn.execute(
                    "INSERT INTO ai_journal (journal_date, run_id, title, "
                    "content_md, market_snapshot, actions_summary, note_type, "
                    "sources_json, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,'close_review',?,?,?,?)",
                    (journal_date,) + args + (now,))
        return {'saved': True, 'journal_date': journal_date,
                'status': status, 'model': model}

    def get_review(self, journal_date: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_journal WHERE journal_date = ? "
                "AND note_type = 'close_review'", (journal_date,)).fetchone()
        return dict(row) if row else None

    def latest_review(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_journal WHERE note_type = 'close_review' "
                "ORDER BY journal_date DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def list_reviews(self, limit: int = 30) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, journal_date, title, status FROM ai_journal "
                "WHERE note_type = 'close_review' "
                "ORDER BY journal_date DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def annotate(self, journal_date: str, note: str,
                 author: str = 'manual') -> bool:
        """追加人工批注（不改 AI 正文；status 置 annotated）。"""
        note = (note or '').strip()
        if not note:
            return False
        now = now_cn().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT annotations FROM ai_journal WHERE journal_date = ?",
                (journal_date,)).fetchone()
            if not row:
                return False
            try:
                items = json.loads(row['annotations'] or '[]')
            except (TypeError, ValueError):
                items = []
            if not isinstance(items, list):
                items = []
            items.append({'author': author, 'note': note, 'at': now})
            conn.execute(
                "UPDATE ai_journal SET annotations=?, status='annotated', "
                "updated_at=? WHERE journal_date=?",
                (json.dumps(items, ensure_ascii=False), now, journal_date))
        return True

    # ── 运行记录 ──────────────────────────────────────────
    def save_run(self, review_date: str, status: str, item_count: int = 0,
                 holdings_hit: int = 0, redline_hits: int = 0,
                 event_count: int = 0, source_failures: int = 0,
                 duration_ms: int = 0, model: str = '',
                 error: str = '') -> None:
        now = now_cn().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO news_review_run (review_date, status, item_count, "
                "holdings_hit, redline_hits, event_count, source_failures, "
                "duration_ms, model, error, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(review_date) DO UPDATE SET status=excluded.status, "
                "item_count=excluded.item_count, holdings_hit=excluded.holdings_hit, "
                "redline_hits=excluded.redline_hits, event_count=excluded.event_count, "
                "source_failures=excluded.source_failures, "
                "duration_ms=excluded.duration_ms, model=excluded.model, "
                "error=excluded.error, updated_at=excluded.updated_at",
                (review_date, status, int(item_count), int(holdings_hit),
                 int(redline_hits), int(event_count), int(source_failures),
                 int(duration_ms), model, error, now, now))

    def get_run(self, review_date: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM news_review_run WHERE review_date = ?",
                (review_date,)).fetchone()
        return dict(row) if row else None


class NewsWeeklyDAO:
    """周度收盘检阅汇总（F2）；与周六观察池复盘分开存。"""

    def __init__(self, conn_factory=None):
        if conn_factory is None:
            from src.models.database import db_conn as conn_factory
        self._conn = conn_factory

    def save(self, week_end: str, week_start: str, title: str,
             content_md: str, sources: list[dict], actions: dict,
             status: str = 'published', model: str = '') -> None:
        now = now_cn().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO news_weekly (week_end, week_start, title, "
                "content_md, sources_json, actions_json, status, model, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(week_end) DO UPDATE SET week_start=excluded.week_start, "
                "title=excluded.title, content_md=excluded.content_md, "
                "sources_json=excluded.sources_json, actions_json=excluded.actions_json, "
                "status=excluded.status, model=excluded.model, "
                "updated_at=excluded.updated_at",
                (week_end, week_start, title, content_md,
                 json.dumps(sources, ensure_ascii=False),
                 json.dumps(actions, ensure_ascii=False),
                 status, model, now, now))

    def get(self, week_end: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM news_weekly WHERE week_end = ?",
                (week_end,)).fetchone()
        return dict(row) if row else None

    def latest(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM news_weekly ORDER BY week_end DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT week_end, week_start, title, status FROM news_weekly "
                "ORDER BY week_end DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]


class NewsEventDAO:
    def __init__(self, conn_factory=None):
        if conn_factory is None:
            from src.models.database import db_conn as conn_factory
        self._conn = conn_factory

    def upsert_many(self, events: list[dict]) -> int:
        if not events:
            return 0
        now = now_cn().isoformat()
        n = 0
        with self._conn() as conn:
            for e in events:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO news_event (event_key, event_time, "
                    "trade_date, level, event_type, code, name, title, fact, "
                    "inference, source, url, news_id, official, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e['event_key'], e['event_time'], e['trade_date'],
                     e['level'], e.get('event_type'), e.get('code', ''),
                     e.get('name'), e['title'], e.get('fact'),
                     e.get('inference'), e.get('source'), e.get('url'),
                     e.get('news_id'), 1 if e.get('official') else 0, now))
                n += cur.rowcount
        return n

    def recent(self, limit: int = 20, trade_date: Optional[str] = None,
               levels: Optional[list[str]] = None) -> list[dict]:
        sql = "SELECT * FROM news_event WHERE 1=1"
        args: list = []
        if trade_date:
            sql += " AND trade_date = ?"
            args.append(trade_date)
        if levels:
            sql += " AND level IN (" + ",".join("?" * len(levels)) + ")"
            args.extend(levels)
        sql += " ORDER BY event_time DESC, id DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def count(self, trade_date: str, level: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM news_event WHERE trade_date = ?"
        args: list = [trade_date]
        if level:
            sql += " AND level = ?"
            args.append(level)
        with self._conn() as conn:
            return int(conn.execute(sql, args).fetchone()['n'])
