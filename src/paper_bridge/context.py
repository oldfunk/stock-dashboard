"""组合上下文（E4，2026-11-28）：给 AI 分析/笔记看的只读组合快照。

- 优先 :8081 面板（实际收益/逐仓未实现），失败回退 E0 只读桥；
- 进程内缓存 300 秒，避免 20 只股票分析重复打面板；
- 只读、无口令、失败返回空字符串（调用方直接跳过）。
"""
from __future__ import annotations

import time
from typing import Optional

_CACHE = {"at": 0.0, "text": ""}
_TTL = 300.0


def _pct(v) -> str:
    return "--" if v is None else f"{v * 100:+.2f}%"


def clear_cache() -> None:
    _CACHE["at"] = 0.0
    _CACHE["text"] = ""


def build_context(panel: Optional[dict] = None,
                  bridge: Optional[dict] = None) -> str:
    """把面板/桥数据压成一段短文本；两边都不可用返回空串。"""
    panel = panel or {}
    bridge = bridge or {}
    actual = (panel.get("performance") or {}) if panel.get("available") else {}
    asof = panel.get("asof") or bridge.get("asof") or "未知"
    positions = panel.get("positions") or bridge.get("positions") or []
    nav = bridge.get("nav") or {}
    if not actual and not positions and not nav:
        return ""

    lines = [f"【组合上下文（只读，asof {asof}；只作背景，不得据此给交易建议）】"]
    if actual:
        lines.append(
            f"- 实际收益：累计 {_pct(actual.get('total_return'))}，"
            f"今日 {_pct(actual.get('today_pnl_pct'))}，"
            f"已实现 {actual.get('realized_pnl') if actual.get('realized_pnl') is not None else '--'}，"
            f"费用 {actual.get('fees_total') if actual.get('fees_total') is not None else '--'}，"
            f"最大回撤 {_pct(actual.get('mdd'))}，仓位 {_pct(actual.get('exposure'))}。")
    elif nav:
        lines.append(
            f"- 净值：总资产 {nav.get('total_value')}，现金 {nav.get('cash')}，"
            f"累计 {_pct(nav.get('pnl_pct'))}。")
    if positions:
        parts = []
        for p in positions[:8]:
            sym = str(p.get("symbol") or "").zfill(6)
            parts.append(
                f"{sym} {p.get('total_volume')}股 成本"
                f"{p.get('avg_cost')} 现价{p.get('current_price')}")
        lines.append("- 持仓：" + "；".join(parts))
    plan = (bridge.get("pending_plan") or {}) if bridge else {}
    if plan:
        lines.append(f"- 待执行计划：#{plan.get('id')}（{plan.get('plan_date')}）。")
    lines.append("- 纪律：组合数据只读；不得反过来写策略或直接下单。")
    return "\n".join(lines)


def portfolio_context(refresh: bool = False) -> str:
    """带 300s 缓存的组合上下文；任何失败返回空串。"""
    now = time.monotonic()
    if not refresh and _CACHE["text"] and now - _CACHE["at"] < _TTL:
        return _CACHE["text"]
    text = ""
    try:
        from src.paper_bridge import load_panel_snapshot
        panel = load_panel_snapshot()
    except Exception:
        panel = {}
    try:
        from src.paper_bridge import load_status
        st = load_status(nav_limit=1, fills_limit=0)
        bridge = {
            "asof": ((st.get("nav") or {}).get("data") or [{}])[0].get("timestamp"),
            "nav": ((st.get("nav") or {}).get("data") or [{}])[0],
            "pending_plan": ((st.get("plan") or {}).get("data") or [None])[0],
            "positions": (st.get("positions") or {}).get("data") or [],
        }
    except Exception:
        bridge = {}
    if panel.get("available"):
        text = build_context(panel=panel, bridge=bridge)
    else:
        # 面板不可用时，至少用桥的 NAV/持仓/计划拼一段
        pos = (st.get("positions") or {}).get("data") if 'st' in locals() else []
        text = build_context(
            bridge={"asof": bridge.get("asof"), "nav": bridge.get("nav") or {},
                    "pending_plan": bridge.get("pending_plan"),
                    "positions": pos or []})
    _CACHE["text"], _CACHE["at"] = text, now
    return text
