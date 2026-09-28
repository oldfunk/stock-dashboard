"""Stock Dashboard 只读富化：基本面快照 + AI 分析历史 + 笔记 + 论点 + 大盘状态。

全部失败回退 {} / []，永不抛错。供 Agent 上下文 enrichment 用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from paper_trading.integration.pool import _norm, _ro
from paper_trading.integration.settings import mother_db_path


def read_stock_cards(codes: list[str], mother: Optional[Path] = None,
                     max_notes: int = 2) -> dict[str, dict]:
    """每只股票一张卡：基本面 + 最新 AI 分析 + 笔记 + 论点（含卖出条件）。"""
    db = mother_db_path(mother)
    if db is None or not codes:
        return {}
    cards: dict[str, dict] = {}
    try:
        conn = _ro(db)
    except Exception:
        return {}
    try:
        for code in codes:
            c = _norm(code)
            if not c:
                continue
            card: dict = {}
            try:
                r = conn.execute(
                    """SELECT name, sector, pe, pb, market_cap, roe, dividend_yield,
                              current_price, high_52w, low_52w, is_st
                       FROM stock_snapshot WHERE code = ?""", (c,)).fetchone()
                if r:
                    card["fundamentals"] = {
                        "name": r["name"], "sector": r["sector"], "pe": r["pe"],
                        "pb": r["pb"], "market_cap_yi": r["market_cap"],
                        "roe": r["roe"], "dividend_yield": r["dividend_yield"],
                        "price_52w": [r["low_52w"], r["high_52w"]],
                        "is_st": bool(r["is_st"]),
                    }
            except Exception:
                pass
            try:
                r = conn.execute(
                    """SELECT analysis_date, score, ai_analysis, ai_trade_strategy, model
                       FROM stock_analysis_history WHERE stock_code = ?
                       ORDER BY analysis_date DESC LIMIT 1""", (c,)).fetchone()
                if r:
                    card["ai_analysis"] = {
                        "date": r["analysis_date"], "score": r["score"],
                        "model": r["model"],
                        "analysis": str(r["ai_analysis"] or "")[:800],
                        "trade_strategy": str(r["ai_trade_strategy"] or "")[:400],
                    }
            except Exception:
                pass
            try:
                rows = conn.execute(
                    """SELECT note, note_type, model FROM watchlist_notes
                       WHERE code = ? ORDER BY id DESC LIMIT ?""",
                    (c, max_notes)).fetchall()
                if rows:
                    card["notes"] = [
                        {"type": r["note_type"], "model": r["model"],
                         "note": str(r["note"] or "")[:400]} for r in rows]
            except Exception:
                pass
            try:
                r = conn.execute(
                    """SELECT core_thesis, sell_conditions FROM watchlist_thesis
                       WHERE code = ?""", (c,)).fetchone()
                if r:
                    card["thesis"] = {
                        "core": str(r["core_thesis"] or "")[:300],
                        "sell_conditions": str(r["sell_conditions"] or "")[:300],
                    }
            except Exception:
                pass
            if card:
                cards[c] = card
        return cards
    finally:
        try:
            conn.close()
        except Exception:
            pass


def read_market_regime(mother: Optional[Path] = None) -> list[dict]:
    """大盘状态：三大指数最新一条（含涨跌幅/PE/PB，给环境过滤器用）。"""
    db = mother_db_path(mother)
    if db is None:
        return []
    try:
        conn = _ro(db)
    except Exception:
        return []
    try:
        out = []
        for code in ("000001", "399001", "399006"):
            try:
                r = conn.execute(
                    """SELECT index_name, current_value, change_percent, pe, pb, date
                       FROM market_index WHERE index_code = ?
                       ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
                if r:
                    out.append({"index": r["index_name"], "value": r["current_value"],
                                "change_pct": r["change_percent"], "pe": r["pe"],
                                "pb": r["pb"], "date": r["date"]})
            except Exception:
                continue
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass
