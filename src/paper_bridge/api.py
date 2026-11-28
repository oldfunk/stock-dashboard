"""纸盘只读 API（E1，2026-11-24）。

`/api/paper/*` 只读子项目账本；失败返回 200 + 对应块 `unavailable`，
不抛 500、不影响母站。前端/外部程序按 `source/asof/stale` 判断可用性。
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from .state import load_nav, load_recent_fills, load_status

router = APIRouter(prefix="/api/paper", tags=["paper-bridge"])


@router.get("/status")
def paper_status():
    """账户/持仓/最近 pending 计划/NAV/最近成交（各块独立降级）。"""
    data = load_status(nav_limit=5, fills_limit=5)
    data["available"] = any(
        isinstance(v, dict) and v.get("source") != "unavailable"
        for v in data.values())
    return {"ok": True, "data": data}


@router.get("/nav")
def paper_nav(limit: int = Query(30, ge=1, le=200)):
    return {"ok": True, "data": load_nav(limit=limit)}


@router.get("/fills")
def paper_fills(limit: int = Query(20, ge=1, le=200)):
    return {"ok": True, "data": load_recent_fills(limit=limit)}
