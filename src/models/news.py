"""新闻舆情 DAO（2026-10-19）。

事实底座：滚动条读最新条目，AI 每日摘要读未摘要条目并回写情绪判定。
写入幂等靠 DB 的 UNIQUE(COALESCE(url,''), title)，重复拉取不膨胀。
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Optional

from src.utils import now_cn


class NewsDAO:
    def __init__(self, conn_factory=None):
        if conn_factory is None:
            from src.models.database import db_conn as conn_factory
        self._conn = conn_factory

    # ── 写入 ──────────────────────────────────────────────
    def upsert_many(self, items: list[dict]) -> int:
        """批量入库（同 URL+标题视为一条，返回新增条数）。"""
        if not items:
            return 0
        now = now_cn().isoformat()
        added = 0
        with self._conn() as conn:
            for it in items:
                title = (it.get('title') or '').strip()
                if not title:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO news_item (code, name, title, summary,"
                    " url, source, published_at, fetched_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (str(it.get('code') or ''), it.get('name'), title,
                     it.get('summary'), it.get('url'), it.get('source'),
                     it.get('published_at'), now))
                added += cur.rowcount
        return added

    # ── 读取 ──────────────────────────────────────────────
    def latest(self, limit: int = 30, only_market: bool = False,
               since: Optional[str] = None) -> list[dict]:
        """最新条目（滚动条用）：按发布时间倒序，缺发布时间的排在后面。"""
        sql = "SELECT * FROM news_item WHERE 1=1"
        args: list = []
        if only_market:
            sql += " AND code = ''"
        if since:
            sql += " AND COALESCE(published_at, fetched_at) >= ?"
            args.append(since)
        sql += (" ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC"
                " LIMIT ?")
        args.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def by_code(self, code: str, limit: int = 20,
                since: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM news_item WHERE code = ?"
        args: list = [code]
        if since:
            sql += " AND COALESCE(published_at, fetched_at) >= ?"
            args.append(since)
        sql += " ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def for_codes(self, codes: list[str], limit_per_code: int = 8,
                  since: Optional[str] = None,
                  unsummarized_only: bool = False) -> list[dict]:
        """多只股票的新闻（AI 摘要输入），按代码分组取最近的。"""
        if not codes:
            return []
        out: list[dict] = []
        for code in codes:
            sql = "SELECT * FROM news_item WHERE code = ?"
            args: list = [code]
            if since:
                sql += " AND COALESCE(published_at, fetched_at) >= ?"
                args.append(since)
            if unsummarized_only:
                sql += " AND summarized_at IS NULL"
            sql += (" ORDER BY COALESCE(published_at, fetched_at) DESC,"
                    " id DESC LIMIT ?")
            args.append(limit_per_code)
            with self._conn() as conn:
                rows = conn.execute(sql, args).fetchall()
            out.extend(dict(r) for r in rows)
        return out

    def mark_fetched(self, codes: list, ok: bool = True) -> None:
        """记录本轮抓取尝试（冷却按它算，与是否有内容无关）。"""
        if not codes:
            return
        now = now_cn().isoformat()
        with self._conn() as conn:
            for c in codes:
                conn.execute(
                    "INSERT INTO news_fetch_log (code, fetched_at, ok_count)"
                    " VALUES (?,?,?) ON CONFLICT(code) DO UPDATE SET"
                    " fetched_at=excluded.fetched_at,"
                    " ok_count=news_fetch_log.ok_count + ?",
                    (c, now, 1 if ok else 0, 1 if ok else 0))

    def recently_fetched(self, codes: list, cooldown_min: int) -> set:
        """返回 codes 里最近 cooldown_min 分钟内已抓过的那些（避免重复打数据源）。

        以库里最新 fetched_at 为准；进程重启后依然有效（不依赖内存状态）。
        """
        if not codes or cooldown_min <= 0:
            return set()
        cutoff = (now_cn() - timedelta(minutes=cooldown_min)).isoformat()
        with self._conn() as conn:
            marks = ",".join("?" * len(codes))
            rows = conn.execute(
                "SELECT code, MAX(fetched_at) AS last FROM news_fetch_log "
                "WHERE code IN (" + marks + ") GROUP BY code",
                list(codes)).fetchall()
        return {r["code"] for r in rows if (r["last"] or "") >= cutoff}

    def unsummarized_count(self, since: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM news_item WHERE summarized_at IS NULL"
        args: list = []
        if since:
            sql += " AND COALESCE(published_at, fetched_at) >= ?"
            args.append(since)
        with self._conn() as conn:
            return int(conn.execute(sql, args).fetchone()['n'])

    # ── AI 摘要回写 ────────────────────────────────────────
    def apply_sentiment(self, judgments: list[dict]) -> int:
        """回写逐条情绪与一句话解读；judgment 形如 {id, sentiment, brief}。"""
        if not judgments:
            return 0
        now = now_cn().isoformat()
        n = 0
        with self._conn() as conn:
            for j in judgments:
                if not j.get('id'):
                    continue
                cur = conn.execute(
                    "UPDATE news_item SET sentiment=?, ai_brief=?, summarized_at=?"
                    " WHERE id=?",
                    (j.get('sentiment'), j.get('brief'), now, j['id']))
                n += cur.rowcount
        return n

    # ── 舆情日报 ──────────────────────────────────────────
    def save_digest(self, digest_date: str, content_md: str, overall: str = '',
                    scope: str = '', model: str = '', item_count: int = 0,
                    usage: Optional[dict] = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO news_digest (digest_date, scope, overall,"
                " content_md, model, item_count, usage_json, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (digest_date, scope, overall, content_md, model, item_count,
                 json.dumps(usage, ensure_ascii=False) if usage else None,
                 now_cn().isoformat()))

    def get_digest(self, digest_date: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM news_digest WHERE digest_date = ?",
                (digest_date,)).fetchone()
        return dict(row) if row else None

    def latest_digest(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM news_digest ORDER BY digest_date DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def digest_dates(self, limit: int = 30) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT digest_date FROM news_digest ORDER BY digest_date DESC"
                " LIMIT ?", (limit,)).fetchall()
        return [r['digest_date'] for r in rows]
