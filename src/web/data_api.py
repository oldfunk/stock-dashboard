"""只读数据 API（2026-10-27）：供子项目（paper_trading）等外部系统调阅母库数据。

原则：
- **只读**：不写母库一字。
- **稳定 JSON 契约**：字段名固定，便于子项目解析（与直接读 SQLite 解耦）。
- **缺失不抛错**：返回空/None，子项目可静默回退。
- 覆盖：筛选候选 / 观察池 / 个股卡 / 全市场检索 / 市场状态 / 收益验证 / 研究账本。

挂载：`src/web/routes.py` 调 `register_data_api(app)`。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from src.models.database import db_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data-api"])


def _rows(sql: str, params: tuple | list = ()) -> list[dict]:
    try:
        with db_conn() as conn:
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
    except Exception as exc:  # noqa: BLE001 - 只读接口不抛错
        logger.warning(f"[data-api] 查询失败 {sql[:60]}: {exc}")
        return []


def _one(sql: str, params: tuple | list = ()) -> dict | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


# ── 选股漏斗 ────────────────────────────────────────────────────────────────

@router.get("/screening")
def screening(limit: int = Query(20, ge=1, le=200), tag: str = "") -> dict:
    """最新一轮全市场筛选的 active 候选（按 score 降序）。"""
    run = _one(
        "SELECT run_id FROM screening_result "
        "ORDER BY run_date DESC, run_id DESC LIMIT 1")
    if not run:
        return {"run_id": None, "items": []}
    sql = ("SELECT code, name, score, pe, pb, roe, reason FROM screening_result "
           "WHERE run_id = ? AND (status IS NULL OR status = 'active')")
    params: list = [run["run_id"]]
    if tag:
        sql += " AND strategy_tags LIKE ?"
        params.append(f"%{tag}%")
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)
    items = [{"symbol": r["code"], "name": r["name"], "score": r["score"],
              "pe": r["pe"], "pb": r["pb"], "roe": r["roe"],
              "reason": r["reason"]} for r in _rows(sql, params)]
    return {"run_id": run["run_id"], "items": items}


@router.get("/watchlist")
def watchlist() -> dict:
    """观察池：手动钉选 + AI 观察池（并集，去重）。"""
    manual = _rows("SELECT code FROM watchlist")
    ai = _rows("SELECT code FROM ai_watchlist")
    return {"symbols": [r["code"] for r in manual],
            "ai_symbols": [r["code"] for r in ai]}


# ── 全市场检索 ──────────────────────────────────────────────────────────────

@router.get("/universe/search")
def universe_search(query: str = Query("", min_length=1), limit: int = Query(3, ge=1, le=20)) -> dict:
    """按代码/名称检索全市场标的（供 AI 提名解析）。"""
    q = (query or "").strip()
    if not q:
        return {"items": []}
    if q.isdigit() and len(q) == 6:
        row = _one("SELECT code, name FROM stock_snapshot WHERE code = ?", (q,))
        if row:
            return {"items": [{"symbol": row["code"], "name": row["name"],
                               "matched": "code"}]}
    row = _one("SELECT code, name FROM stock_snapshot WHERE name = ? "
               "ORDER BY code LIMIT 1", (q,))
    if row:
        return {"items": [{"symbol": row["code"], "name": row["name"],
                           "matched": "name"}]}
    rows = _rows("SELECT code, name FROM stock_snapshot WHERE name LIKE ? "
                 "ORDER BY code LIMIT ?", (f"%{q}%", limit))
    if rows:
        return {"items": [{"symbol": r["code"], "name": r["name"],
                           "matched": "name~"}]}
    return {"items": []}


@router.get("/universe/size")
def universe_size() -> dict:
    row = _one("SELECT COUNT(*) AS n FROM stock_snapshot")
    return {"count": row["n"] if row else 0}


# ── 个股卡 ──────────────────────────────────────────────────────────────────

@router.get("/stock/{code}")
def stock_card(code: str) -> dict:
    """个股卡：基本面 + 最新 AI 分析 + 笔记 + 论点。"""
    code = str(code or "").strip().zfill(6)
    snap = _one("SELECT name, sector, pe, pb, market_cap, roe, dividend_yield, "
                "current_price, high_52w, low_52w, is_st FROM stock_snapshot "
                "WHERE code = ?", (code,))
    card: dict[str, Any] = {"symbol": code}
    if snap:
        card["fundamentals"] = {
            "name": snap["name"], "sector": snap["sector"], "pe": snap["pe"],
            "pb": snap["pb"], "market_cap_yi": snap["market_cap"],
            "roe": snap["roe"], "dividend_yield": snap["dividend_yield"],
            "price_52w": [snap["low_52w"], snap["high_52w"]],
            "is_st": bool(snap["is_st"]),
        }
    analysis = _one("SELECT analysis_date, score, ai_analysis, ai_trade_strategy, "
                    "model FROM stock_analysis_history WHERE stock_code = ? "
                    "ORDER BY analysis_date DESC LIMIT 1", (code,))
    if analysis:
        card["ai_analysis"] = {
            "date": analysis["analysis_date"], "score": analysis["score"],
            "model": analysis["model"],
            "analysis": str(analysis["ai_analysis"] or "")[:800],
            "trade_strategy": str(analysis["ai_trade_strategy"] or "")[:400],
        }
    notes = _rows("SELECT note, note_type, model FROM watchlist_notes "
                  "WHERE code = ? ORDER BY id DESC LIMIT 2", (code,))
    if notes:
        card["notes"] = [{"type": r["note_type"], "model": r["model"],
                          "note": str(r["note"] or "")[:400]} for r in notes]
    thesis = _one("SELECT core_thesis, sell_conditions FROM watchlist_thesis "
                  "WHERE code = ?", (code,))
    if thesis:
        card["thesis"] = {"core": str(thesis["core_thesis"] or "")[:300],
                          "sell_conditions": str(thesis["sell_conditions"] or "")[:300]}
    return card


# ── 市场状态（R1） ──────────────────────────────────────────────────────────

@router.get("/market_state")
def market_state(date: str = "") -> dict:
    """某日市场状态向量 + regime（缺失字段显式 NULL）。"""
    if date:
        row = _one("SELECT * FROM market_state_daily WHERE trade_date = ?", (date,))
    else:
        row = _one("SELECT * FROM market_state_daily ORDER BY trade_date DESC LIMIT 1")
    if not row:
        return {"trade_date": date, "metrics": {}, "missing": [],
                "coverage": 0.0, "status": "unavailable", "regime": None}
    import json as _json
    return {
        "trade_date": row["trade_date"],
        "metrics": _json.loads(row["metrics_json"] or "{}"),
        "missing": _json.loads(row["missing_json"] or "[]"),
        "coverage": row["coverage"],
        "status": row["status"],
        "regime": row.get("regime"),
        "regime_confidence": row.get("regime_confidence"),
    }


@router.get("/market_index")
def market_index() -> dict:
    """三大指数最新一条（含涨跌幅/PE/PB）。"""
    out = []
    for code in ("000001", "399001", "399006"):
        row = _one("SELECT index_name, current_value, change_percent, pe, pb, date "
                   "FROM market_index WHERE index_code = ? "
                   "ORDER BY date DESC LIMIT 1", (code,))
        if row:
            out.append({"index": row["index_name"], "value": row["current_value"],
                        "change_pct": row["change_percent"], "pe": row["pe"],
                        "pb": row["pb"], "date": row["date"]})
    return {"items": out}


# ── 收益验证（R2 数据集） ────────────────────────────────────────────────────

@router.get("/outcomes")
def outcomes(code: str = "", start: str = "", end: str = "",
             limit: int = Query(200, ge=1, le=2000)) -> dict:
    """前向收益验证（analysis_outcome）：信号/置信/安全边际/各窗口收益与超额。"""
    sql = ("SELECT stock_code, analysis_date, signal, confidence, mos_at_entry, "
           "ret_20, ret_60, ret_120, excess_20, excess_60, excess_120 "
           "FROM analysis_outcome WHERE 1=1")
    params: list = []
    if code:
        sql += " AND stock_code = ?"
        params.append(code)
    if start:
        sql += " AND analysis_date >= ?"
        params.append(start)
    if end:
        sql += " AND analysis_date <= ?"
        params.append(end)
    sql += " ORDER BY analysis_date DESC LIMIT ?"
    params.append(limit)
    return {"items": _rows(sql, params)}


# ── 研究账本（R0/R2） ───────────────────────────────────────────────────────

@router.get("/research/hypotheses")
def research_hypotheses(status: str = "", limit: int = Query(50, ge=1, le=500)) -> dict:
    from src.research.dao import ResearchDAO
    try:
        items = ResearchDAO().list_hypotheses(status or None, limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[data-api] hypotheses 失败: {exc}")
        items = []
    return {"items": items}


@router.get("/research/experiments")
def research_experiments(hypothesis_id: str = "",
                         limit: int = Query(50, ge=1, le=500)) -> dict:
    from src.research.dao import ResearchDAO
    try:
        items = ResearchDAO().list_experiments(hypothesis_id or None, None, limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[data-api] experiments 失败: {exc}")
        items = []
    return {"items": items}


@router.get("/research/decisions")
def research_decisions(status: str = "", limit: int = Query(50, ge=1, le=500)) -> dict:
    from src.research.dao import ResearchDAO
    try:
        items = ResearchDAO().list_decisions(status or None, limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[data-api] decisions 失败: {exc}")
        items = []
    return {"items": items}


@router.get("/research/market_states")
def research_market_states(limit: int = Query(30, ge=1, le=500)) -> dict:
    from src.research.dao import ResearchDAO
    try:
        items = ResearchDAO().list_market_states(limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[data-api] market_states 失败: {exc}")
        items = []
    return {"items": items}


def register_data_api(app) -> None:
    """把只读数据 API 挂到 FastAPI app。"""
    app.include_router(router)
