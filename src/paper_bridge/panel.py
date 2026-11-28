"""子项目面板（:8081）只读客户端（2026-11-28）。

收盘检阅需要看到"投资实际收益"，而这些口径由子项目面板计算（FIFO 已实现、
费用、回撤、暴露等），母库只读桥拿不到全部。这里只发 GET、只读、失败降级：
- 默认 `http://127.0.0.1:8081`，可用 `PAPER_PANEL_URL` 覆盖；
- 超时 2s，任何异常返回 `available=False`，由调用方回退到 E0 只读桥；
- 不发送口令、不调用任何写接口。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PANEL_URL = "http://127.0.0.1:8081"
KEEP_PERF = (
    "total_return", "total_pnl", "total_value", "cash", "market_value",
    "today_pnl", "today_pnl_pct", "mdd", "mdd_date", "current_drawdown",
    "vol_annual", "sharpe", "win_days", "lose_days", "exposure",
    "realized_pnl", "fees_total", "win_trades", "lose_trades",
    "closed_trades", "win_rate", "estimated",
)
KEEP_POS = ("symbol", "total_volume", "available_volume", "avg_cost",
            "current_price", "market_value", "entry_day", "price_asof")


def panel_base() -> str:
    return (os.environ.get("PAPER_PANEL_URL") or DEFAULT_PANEL_URL).rstrip("/")


def _get(path: str, timeout: float = 2.0) -> Optional[dict]:
    url = panel_base() + path
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("data") if isinstance(body, dict) and body.get("ok") else None
    except Exception as e:                       # noqa: BLE001
        logger.debug("[面板] %s 不可用: %s", path, e)
        return None


def load_panel_snapshot(timeout: float = 2.0) -> dict:
    """读取 :8081 的绩效/概览/持仓；三个 GET 都失败时 available=False。"""
    perf = _get("/api/performance", timeout=timeout)
    if not perf:
        return {"available": False, "source": "paper:panel",
                "reason": "panel unavailable"}
    overview = _get("/api/overview", timeout=timeout) or {}
    positions = _get("/api/positions", timeout=timeout) or []
    if not positions:
        positions = (overview.get("status") or {}).get("positions") or []
    summary = perf.get("summary") or {}
    status = overview.get("status") or {}
    asof = status.get("data_asof") or status.get("timestamp") or ""
    return {
        "available": True,
        "source": "paper:panel",
        "asof": asof,
        "performance": {k: summary.get(k) for k in KEEP_PERF},
        "today": {
            "date": overview.get("date"),
            "data_asof": status.get("data_asof"),
            "estimated": overview.get("estimated"),
            "today_pnl": summary.get("today_pnl"),
            "today_pnl_pct": summary.get("today_pnl_pct"),
        },
        "positions": [{k: p.get(k) for k in KEEP_POS} for p in positions
                      if isinstance(p, dict)],
    }
