"""全市场标的检索：把 AI 提名的自由文本（名称/代码）解析成合法 A 股代码。

数据源：Stock Dashboard 的 `stock_snapshot`（全市场约 5500 只的代码-名称表，
只读）。母库缺席时退化为本地规则（6 位代码正则 + 本地 stock_pool 名称表），
再不行返回空 —— 绝不抛错阻断决策链。

永不写母库：只开 `mode=ro`。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from paper_trading.integration.pool import _norm, _ro
from paper_trading.integration.settings import mother_db_path

# A 股代码前缀：沪主板 6 / 深主板 0 / 创业板 3 / 科创 688 / 北交 8、4
_VALID_PREFIX = ("60", "68", "00", "30", "8", "4", "9")
_NOISE_WORDS = ("指数", "ETF", "etf", "可转债", "转债", "基金", "LOF", "lof")


def is_a_share(code: str) -> bool:
    """是否为形态合法的 A 股 6 位代码（不做存在性校验）。"""
    c = _norm(code)
    return len(c) == 6 and c.isdigit() and c.startswith(_VALID_PREFIX)


def _clean_query(q: str) -> str:
    t = str(q or "").strip()
    t = re.sub(r"[（(].*?[)）]", "", t)          # 去括号注释
    t = re.sub(r"\s+", "", t)
    return t.strip("·、,，。.:：;；-—")


def search_symbols(queries, per_query: int = 3,
                   mother: Optional[Path] = None,
                   local_names: Optional[dict] = None) -> list[dict]:
    """把若干自由文本查询解析为 [{symbol, name, query, matched}]。

    匹配优先级：6位代码精确 → 名称精确 → 名称模糊（LIKE，限 per_query 条）。
    每条查询取第一个合法命中；同名多条时以母库顺序（代码升序）稳定取第一条。
    """
    if isinstance(queries, str):
        queries = [queries]
    out: list[dict] = []
    seen: set[str] = set()
    db = mother_db_path(mother)
    conn = None
    if db is not None:
        try:
            conn = _ro(db)
        except Exception:
            conn = None
    try:
        for raw in (queries or []):
            q = _clean_query(raw)
            if not q or any(w in q for w in _NOISE_WORDS):
                continue
            hit = _match_one(conn, q, per_query) or _match_local(local_names, q)
            if not hit:
                continue
            sym = _norm(hit["symbol"])
            if not is_a_share(sym) or sym in seen:
                continue
            seen.add(sym)
            out.append({"symbol": sym, "name": hit.get("name") or "",
                        "query": q, "matched": hit.get("matched", "")})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _match_one(conn: Optional[sqlite3.Connection], q: str,
               per_query: int) -> Optional[dict]:
    if conn is None:
        return None
    try:
        if q.isdigit() and len(q) == 6:
            r = conn.execute(
                "SELECT code, name FROM stock_snapshot WHERE code = ?", (q,)).fetchone()
            return ({"symbol": r["code"], "name": r["name"], "matched": "code"}
                    if r else None)
        r = conn.execute(
            "SELECT code, name FROM stock_snapshot WHERE name = ? ORDER BY code LIMIT 1",
            (q,)).fetchone()
        if r:
            return {"symbol": r["code"], "name": r["name"], "matched": "name"}
        rows = conn.execute(
            "SELECT code, name FROM stock_snapshot WHERE name LIKE ? ORDER BY code LIMIT ?",
            (f"%{q}%", per_query)).fetchall()
        if rows:
            return {"symbol": rows[0]["code"], "name": rows[0]["name"],
                    "matched": "name~"}
    except Exception:
        return None
    return None


def _match_local(local_names: Optional[dict], q: str) -> Optional[dict]:
    """母库缺席时的退化匹配：仅认本地已知代码/名称，绝不猜。"""
    names = local_names or {}
    if q.isdigit() and len(q) == 6 and q in names:
        return {"symbol": q, "name": names[q], "matched": "local"}
    for sym, nm in names.items():
        if nm == q:
            return {"symbol": sym, "name": nm, "matched": "local"}
    for sym, nm in names.items():
        if q and nm and q in nm:
            return {"symbol": sym, "name": nm, "matched": "local~"}
    return None


def universe_size(mother: Optional[Path] = None) -> int:
    """母库可检索的全市场标的数（面板展示用；不可用返回 0）。"""
    db = mother_db_path(mother)
    if db is None:
        return 0
    try:
        conn = _ro(db)
    except Exception:
        return 0
    try:
        r = conn.execute("SELECT COUNT(*) FROM stock_snapshot").fetchone()
        return int(r[0]) if r else 0
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass