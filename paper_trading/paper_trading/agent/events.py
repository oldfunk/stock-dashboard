"""事件驱动 AI 复盘：盘中异动才叫 LLM，平时零调用。

不是 tick/HFT，也不是让 LLM 每分钟盯盘：
- cron 只做低频轮询（例如 5 分钟）发现价格/回撤事件；
- 事件命中后才调一次模型，让模型产出**条件单**；
- 条件单由规则引擎每分钟执行，AI 不参与逐笔撮合。

三层冷却防止模型被行情噪音拖垮：全局最小间隔、单标冷却、每日调用预算。
所有状态落 `engine_state`，重启不丢。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from paper_trading.engine.state import EngineState


@dataclass
class EventConfig:
    enabled: bool = True
    price_move_pct: float = 3.0       # 日内涨跌幅绝对值达到该值算异动
    drawdown_pct: float = 5.0          # 持仓自峰值回撤达到该值算异动
    cooldown_min: int = 30             # 同一标的两次 AI 复盘的最小间隔
    min_interval_min: int = 15         # 任意两次 AI 复盘的最小间隔
    max_calls_per_day: int = 6         # 每日最多调用几次模型
    max_conditions: int = 3            # 单次复盘最多挂几条条件单
    max_symbols: int = 3               # 单次最多把几只异动标的交给模型

    @classmethod
    def from_agent(cls, cfg) -> "EventConfig":
        def _f(name: str, default: float) -> float:
            try:
                return float(getattr(cfg, name, default))
            except (TypeError, ValueError):
                return default

        def _i(name: str, default: int) -> int:
            try:
                return int(getattr(cfg, name, default))
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=bool(getattr(cfg, "event_enabled", True)),
            price_move_pct=_f("event_price_move_pct", 3.0),
            drawdown_pct=_f("event_drawdown_pct", 5.0),
            cooldown_min=_i("event_cooldown_min", 30),
            min_interval_min=_i("event_min_interval_min", 15),
            max_calls_per_day=_i("event_max_calls_per_day", 6),
            max_conditions=_i("event_max_conditions", 3),
        )


def detect_events(quotes: dict, positions: list, cfg: EventConfig,
                  now: Optional[datetime] = None) -> list[dict]:
    """纯函数：把实时报价/持仓映射成异动事件（不做任何 LLM/写库）。

    同一标的同时命中涨跌幅和回撤时，保留严重度更高的一个，避免重复调用。
    """
    del now  # 保留参数便于以后加入时段/成交量事件，当前判定不依赖时钟
    pos_by_sym = {getattr(p, "symbol", ""): p for p in (positions or [])}
    best: dict[str, dict] = {}
    for sym, q in (quotes or {}).items():
        if not isinstance(q, dict):
            continue
        try:
            price = float(q.get("price") or 0)
            chg = float(q.get("change_pct") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        candidates: list[dict] = []
        if abs(chg) >= abs(float(cfg.price_move_pct)):
            candidates.append({
                "symbol": sym, "type": "price_move",
                "severity": abs(chg), "price": price, "change_pct": chg,
                "why": f"日内涨跌 {chg:+.2f}%",
            })
        pos = pos_by_sym.get(sym)
        if pos is not None:
            try:
                peak = float(getattr(pos, "peak_price", 0) or 0)
                cost = float(getattr(pos, "avg_cost", 0) or 0)
            except (TypeError, ValueError):
                peak, cost = 0.0, 0.0
            peak = max(peak, cost)
            if peak > 0:
                drop = (peak - price) / peak * 100.0
                if drop >= abs(float(cfg.drawdown_pct)):
                    candidates.append({
                        "symbol": sym, "type": "drawdown",
                        "severity": drop, "price": price, "peak": peak,
                        "change_pct": chg,
                        "why": f"自峰值 {peak:.2f} 回撤 {drop:.2f}%",
                    })
        for ev in candidates:
            old = best.get(sym)
            if old is None or float(ev["severity"]) > float(old["severity"]):
                best[sym] = ev
    return sorted(best.values(), key=lambda e: float(e["severity"]), reverse=True)


def format_events(events: list[dict]) -> str:
    lines = []
    for e in events:
        try:
            lines.append(f"- {e['symbol']} 现价 {float(e['price']):.2f}：{e['why']}")
        except (KeyError, TypeError, ValueError):
            lines.append(f"- {e.get('symbol', '')}：{e.get('why', '')}")
    return "\n".join(lines)


def _recent(last: str, now: datetime, minutes: int) -> bool:
    if not last or minutes <= 0:
        return False
    try:
        prev = datetime.fromisoformat(str(last))
    except ValueError:
        return False
    if prev.tzinfo is None and now.tzinfo is not None:
        prev = prev.replace(tzinfo=now.tzinfo)
    if prev.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=prev.tzinfo)
    return (now - prev) < timedelta(minutes=minutes)


def run_event_review(bridge, symbols: list[str], *, now: Optional[datetime] = None,
                     dry_run: bool = False, force: bool = False,
                     cfg: Optional[EventConfig] = None,
                     trader=None, quotes: Optional[dict] = None) -> dict:
    """检查异动 → 冷却/预算 → AI 产出条件单。

    `trader`/`quotes` 可注入，便于离线单测；生产调用不需要传。
    """
    now = now or datetime.now()
    day = now.date().isoformat()
    agent_cfg = getattr(bridge, "agent_cfg", None)
    cfg = cfg or EventConfig.from_agent(agent_cfg)
    if not force and not dry_run:
        if not cfg.enabled:
            return {"ok": True, "skipped": "event-review-disabled", "timestamp": now.isoformat()}
        if not bool(getattr(agent_cfg, "enabled", True)):
            return {"ok": True, "skipped": "agent-disabled", "timestamp": now.isoformat()}

    from paper_trading.data import realtime as rt

    if not force and not dry_run and not rt.is_trading_session(now):
        return {"ok": True, "skipped": "not-trading-session",
                "timestamp": now.isoformat()}

    positions = bridge.broker.get_all_positions()
    pool = list(dict.fromkeys([*(symbols or []),
                               *[p.symbol for p in positions]]))
    if not pool:
        return {"ok": True, "skipped": "empty-pool", "timestamp": now.isoformat()}
    if quotes is None:
        try:
            quotes = rt.get_quotes(pool, force=True) or {}
        except Exception:                      # noqa: BLE001 - 行情失败不阻断 tick
            quotes = {}
    if not quotes:
        return {"ok": True, "skipped": "no-quotes", "timestamp": now.isoformat()}

    events = detect_events(quotes, positions, cfg, now)
    if not events:
        return {"ok": True, "skipped": "no-events", "timestamp": now.isoformat(),
                "n_quotes": len(quotes)}

    state = EngineState(getattr(bridge, "account_db_path", "paper_account.db"))
    budget_key = f"event:budget:{day}"
    used = int(state.get(budget_key, 0) or 0)
    if not force and cfg.max_calls_per_day > 0 and used >= cfg.max_calls_per_day:
        return {"ok": True, "skipped": "daily-budget", "used": used,
                "events": events, "timestamp": now.isoformat()}
    last_global = str(state.get("event:last", "") or "")
    if not force and _recent(last_global, now, cfg.min_interval_min):
        return {"ok": True, "skipped": "min-interval", "events": events,
                "timestamp": now.isoformat()}

    selected: list[dict] = []
    for e in events:
        sym = str(e.get("symbol") or "")
        if not sym:
            continue
        if not force and _recent(str(state.get(f"event:sym:{sym}", "") or ""),
                                 now, cfg.cooldown_min):
            continue
        selected.append(e)
        if len(selected) >= max(1, cfg.max_symbols):
            break
    if not selected:
        return {"ok": True, "skipped": "symbol-cooldown", "events": events,
                "timestamp": now.isoformat()}

    if trader is None:
        from paper_trading.agent.loop import AgentTrader

        trader = AgentTrader(bridge, agent_cfg)
    event_txt = format_events(selected)
    try:
        res = trader.review_conditions(
            [e["symbol"] for e in selected], event_txt=event_txt,
            dry_run=dry_run, max_conds=cfg.max_conditions)
    except Exception as e:                    # noqa: BLE001 - 事件复盘不能打死 tick
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if not dry_run:
        state.set("event:last", now.isoformat(timespec="seconds"))
        state.set(budget_key, used + 1)
        for e in selected:
            state.set(f"event:sym:{e['symbol']}",
                      now.isoformat(timespec="seconds"))
    bridge.broker.log_operation(
        "ai:event",
        {"events": events, "selected": [e["symbol"] for e in selected],
         "dry_run": bool(dry_run), "force": bool(force)},
        bool(res.get("ok")),
        {"review": {k: v for k, v in res.items() if k != "raw"},
         "raw": str(res.get("raw") or "")[:800]},
        None, None)
    return {"ok": bool(res.get("ok")), "events": events,
            "selected": [e["symbol"] for e in selected],
            "review": res, "dry_run": bool(dry_run),
            "timestamp": now.isoformat()}
