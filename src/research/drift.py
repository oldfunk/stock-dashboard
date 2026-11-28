"""R3-5 漂移检测：滚动超额/回撤/暴露 → decision_log。

只读归因表与纸盘面板；触发时写一条 open 决策（review_date +7 天），
不自动改策略、不改交易。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from src.research.dao import ResearchDAO
from src.research.domain import next_id
from src.utils import now_cn


def rolling_excess(rows: list[dict], window: int = 4) -> Optional[float]:
    vals = [float(r["excess_return"]) for r in rows
            if r.get("excess_return") is not None][:int(window)]
    return (sum(vals) / len(vals)) if vals else None


def drift_signals(attributions: list[dict], panel: Optional[dict] = None,
                  window: int = 4, min_excess: float = 0.0,
                  max_drawdown: float = 0.10,
                  max_exposure: float = 0.80,
                  min_win_rate: float = 0.30,
                  data_quality: Optional[dict] = None) -> list[dict]:
    out = []
    ex = rolling_excess(attributions, window)
    if ex is not None and ex < min_excess:
        out.append({"type": "negative_excess",
                    "value": round(ex, 6),
                    "threshold": min_excess,
                    "why": f"近 {window} 期平均超额 {ex * 100:+.2f}% < 0"})
    summary = (panel or {}).get("performance") or {}
    dd = summary.get("current_drawdown")
    if dd is not None and float(dd) > max_drawdown:
        out.append({"type": "drawdown",
                    "value": float(dd), "threshold": max_drawdown,
                    "why": f"当前回撤 {float(dd) * 100:.2f}% > {max_drawdown * 100:.0f}%"})
    exp = summary.get("exposure")
    if exp is not None and float(exp) > max_exposure:
        out.append({"type": "over_exposure",
                    "value": float(exp), "threshold": max_exposure,
                    "why": f"仓位 {float(exp) * 100:.1f}% > {max_exposure * 100:.0f}%"})
    closed = int(summary.get("closed_trades") or 0)
    win = summary.get("win_rate")
    if closed >= 2 and win is not None and float(win) < min_win_rate:
        out.append({"type": "low_win_rate",
                    "value": float(win), "threshold": min_win_rate,
                    "why": f"已平仓 {closed} 笔胜率 {float(win) * 100:.0f}%"
                           f" < {min_win_rate * 100:.0f}%"})
    cov = (data_quality or {}).get("market_state_coverage")
    if cov is not None and float(cov) < 0.90:
        out.append({"type": "data_quality",
                    "value": float(cov), "threshold": 0.90,
                    "why": f"market_state 覆盖 {float(cov) * 100:.1f}% < 90%"})
    return out


def run_drift(deployment_id: str, window: int = 4,
              dry_run: bool = False) -> dict:
    from src.paper_bridge import load_panel_snapshot

    dao = ResearchDAO()
    rows = dao.list_attributions(deployment_id, limit=50)
    try:
        panel = load_panel_snapshot()
    except Exception:
        panel = {}
    coverage = None
    try:
        from src.models.database import db_conn
        with db_conn() as conn:
            row = conn.execute(
                "SELECT coverage FROM market_state_daily "
                "ORDER BY trade_date DESC LIMIT 1").fetchone()
        coverage = row["coverage"] if row else None
    except Exception:
        coverage = None
    signals = drift_signals(rows, panel, window,
                            data_quality={"market_state_coverage": coverage})
    result = {"deployment_id": deployment_id, "window": window,
              "attributions": len(rows),
              "signals": signals, "decision_id": None}
    if signals and not dry_run:
        today = date.today()
        # 同日同部署只留一条 drift 决策，重复运行不刷屏
        for d in dao.list_decisions(limit=200):
            if (d.get("type") == "drift_alert"
                    and d.get("target") == deployment_id
                    and d.get("decision_date") == today.isoformat()):
                result["decision_id"] = d["id"]
                return result
        existing = [d["id"] for d in dao.list_decisions(limit=200)]
        did = next_id("DL", today, existing)
        dao.insert_decision({
            "id": did,
            "decision_date": today.isoformat(),
            "type": "drift_alert",
            "target": deployment_id,
            "change": "review",
            "rationale": "；".join(s["why"] for s in signals),
            "evidence": {"signals": signals, "attributions": len(rows)},
            "expected": "一周内复核归因与仓位，确认是否降风险",
            "review_date": (today + timedelta(days=7)).isoformat(),
            "status": "open",
            "outcome": "",
            "created_at": now_cn().isoformat(timespec="seconds"),
        })
        result["decision_id"] = did
    return result
