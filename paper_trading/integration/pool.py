"""股票池来源：config（默认，独立模式）+ Stock Dashboard 只读（合并模式）。

--pool-from config      只用 config.yaml（默认；母项目不存在时自动回退到此）
--pool-from watchlist   母库观察池（ai_watchlist + watchlist 并集）
--pool-from screening   母库最新一轮筛选 active 候选（按 score 降序，限 N 只）
--pool-from all         config + watchlist + screening 全并集
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from paper_trading.integration.settings import mother_db_path


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _norm(code) -> str:
    t = str(code or "").strip()
    if t.isdigit():
        t = t.zfill(6)
    m = re.search(r"(\d{6})", t)
    return m.group(1) if m else ""


def _read_watchlist(db: Path) -> list[str]:
    out: list[str] = []
    conn = _ro(db)
    try:
        for table in ("ai_watchlist", "watchlist"):
            try:
                rows = conn.execute(f"SELECT code FROM {table}").fetchall()
            except Exception:
                continue
            for r in rows:
                c = _norm(r["code"])
                if c and c not in out:
                    out.append(c)
    finally:
        conn.close()
    return out


def _read_screening(db: Path, limit: int = 20, tag: str = "") -> list[dict]:
    """最新一轮 active 候选，含 score/reason（给 LLM 做选择依据）。

    tag 非空时按 strategy_tags 模糊匹配；该列为空则无匹配→返回空，
    调用方回退处理（不清池、不报错）。
    """
    conn = _ro(db)
    try:
        row = conn.execute(
            "SELECT run_id FROM screening_result ORDER BY run_date DESC, run_id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        sql = """SELECT code, name, score, pe, pb, roe, reason FROM screening_result
                 WHERE run_id = ? AND (status IS NULL OR status = 'active')"""
        params: list = [row["run_id"]]
        if tag:
            sql += " AND strategy_tags LIKE ?"
            params.append(f"%{tag}%")
        sql += " ORDER BY score DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            c = _norm(r["code"])
            if c:
                out.append({"symbol": c, "name": r["name"], "score": r["score"],
                            "pe": r["pe"], "pb": r["pb"], "roe": r["roe"],
                            "reason": r["reason"]})
        return out
    except Exception:
        return []
    finally:
        conn.close()


def resolve_pool(symbols_arg: Optional[list[str]],
                 config_pool: list[str],
                 pool_from: str = "config",
                 pool_limit: int = 20,
                 mother: Optional[Path] = None,
                 tag: str = "") -> tuple[list[str], str, list[dict]]:
    """解析最终股票池。

    Returns:
        (symbols, source_note, candidates) — candidates 仅 screening 模式有明细；
        母库不可用时静默回退 config，并在 note 中说明。
    """
    if symbols_arg:
        return ([_norm(s) for s in symbols_arg if _norm(s)] or list(config_pool),
                "cli", [])
    if pool_from in ("config", None, ""):
        return list(config_pool), "config", []
    db = mother_db_path(mother)
    if db is None:
        return list(config_pool), "config(fallback:母库不可用)", []
    if pool_from == "watchlist":
        wl = _read_watchlist(db)
        syms = wl or list(config_pool)
        return syms, ("watchlist" if wl else "config(fallback:观察池为空)"), []
    if pool_from == "screening":
        cands = _read_screening(db, pool_limit, tag)
        if not cands:
            hint = f"tag={tag} " if tag else ""
            return list(config_pool), f"config(fallback:无筛选结果{hint})", []
        note = f"screening(最新一轮Top{len(cands)}{',tag=' + tag if tag else ''})"
        return [c["symbol"] for c in cands], note, cands
    if pool_from == "all":
        cands = _read_screening(db, pool_limit, tag)
        merged = list(config_pool)
        for c in cands:
            if c["symbol"] not in merged:
                merged.append(c["symbol"])
        for c in _read_watchlist(db):
            if c not in merged:
                merged.append(c)
        return merged, "all(config+screening+watchlist)", cands
    return list(config_pool), "config", []
