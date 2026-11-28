"""B2：任意一天的“为什么买/没买/卖”审计。

把同一天的：
- 计划（`agent_plans`，plan_date=当天；含 actions/rejected/健康度/额度）
- 决策（`agent_decisions`，decision_date=当天；含执行状态与拒因）
- 意图队列（`rq_intents`，created_at 当天；rqalpha 底座）
- 成交（`fills`，timestamp 当天）
- 条件单（`conditional_orders`：当天创建/当天成交）
- 操作流水（`op_log` 当天）
按标的串成时间线，并给出每条计划动作的最终归宿。

只读；缺数据如实空，不推断“没买=看空”。所有字段都带来源表名，便于回查。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _ro(path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _q(conn, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _jload(s):
    try:
        return json.loads(s) if s else None
    except (TypeError, ValueError):
        return None


def _plan_actions(plan: dict) -> list[dict]:
    return [a for a in (plan.get("actions") or []) if isinstance(a, dict)]


def audit_day(bridge, day: str) -> dict:
    db = bridge.account_db_path
    conn = _ro(db)
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        plans = _q(conn,
                   "SELECT * FROM agent_plans WHERE plan_date = ? ORDER BY id",
                   (day,))
        decisions = _q(conn,
                       "SELECT * FROM agent_decisions WHERE decision_date = ?"
                       " ORDER BY id", (day,))
        fills = _q(conn, "SELECT * FROM fills WHERE substr(timestamp,1,10)=?"
                   " ORDER BY fill_id", (day,))
        conds = (_q(conn,
                    "SELECT * FROM conditional_orders WHERE day = ?"
                    " OR substr(created_at,1,10)=? OR substr(filled_at,1,10)=?"
                    " ORDER BY id", (day, day, day))
                 if "conditional_orders" in tables else [])
        ops = _q(conn, "SELECT * FROM op_log WHERE substr(timestamp,1,10)=?"
                 " ORDER BY id", (day,))
        intents = (_q(conn, "SELECT * FROM rq_intents"
                            " WHERE substr(created_at,1,10)=?"
                            " ORDER BY created_at, rowid", (day,))
                   if "rq_intents" in tables else [])
    finally:
        conn.close()

    for p in plans:
        p["plan"] = _jload(p.get("plan_json")) or {}
    for d in decisions:
        d["actions"] = _jload(d.get("actions_json")) or []
        d["conditions"] = _jload(d.get("conditions_json")) or []
        d["result"] = _jload(d.get("result_json")) or {}

    # 最新决策按 (symbol, action) 建索引（同日可能多条：计划执行 + 事件复盘）
    dec_idx: dict[tuple[str, str], dict] = {}
    for d in decisions:
        for a in d["actions"]:
            key = (str(a.get("symbol")), str(a.get("action") or "").lower())
            dec_idx[key] = a

    timeline: dict[str, dict] = {}

    def slot(sym: str) -> dict:
        return timeline.setdefault(sym, {
            "symbol": sym, "plans": [], "decisions": [], "intents": [],
            "fills": [], "conditions": [], "why": [],
            "sources": ["agent_plans", "agent_decisions", "rq_intents",
                        "fills", "conditional_orders", "op_log"]})

    for p in plans:
        plan = p["plan"]
        for a in _plan_actions(plan):
            sym = str(a.get("symbol") or "")
            if not sym:
                continue
            rec = slot(sym)
            item = {"source": "agent_plans", "plan_id": p["id"],
                    "plan_date": p["plan_date"], "status": p["status"],
                    "action": str(a.get("action") or "").lower(),
                    "volume": a.get("volume"), "price": a.get("price"),
                    "reason": a.get("reason"), "thesis": a.get("thesis"),
                    "strategy": a.get("strategy"),
                    "basis": a.get("basis") or []}
            rec["plans"].append(item)
            dec = dec_idx.get((sym, item["action"]))
            if dec:
                rec["why"].append({
                    "phase": "plan→execute", "action": item["action"],
                    "plan_reason": item.get("reason"),
                    "exec_status": dec.get("status"),
                    "exec_reason": dec.get("reason"),
                    "source": "agent_decisions"})
            else:
                rec["why"].append({
                    "phase": "plan→execute", "action": item["action"],
                    "plan_reason": item.get("reason"),
                    "exec_status": "未找到同日执行记录（可能未开盘/计划被替代/未运行）",
                    "source": "agent_plans"})
        for a in (plan.get("actions_rejected") or []):
            if not isinstance(a, dict):
                continue
            sym = str(a.get("symbol") or "")
            if not sym:
                continue
            slot(sym)["why"].append({
                "phase": "plan-rejected", "action": str(a.get("action") or ""),
                "reason": a.get("reject_reason") or a.get("reason"),
                "source": "agent_plans.actions_rejected"})
        # 计划时点的健康度/额度原样携带（可回放 AI 的输入）
        for a in _plan_actions(plan):
            sym = str(a.get("symbol") or "")
            if sym:
                slot(sym)["plan_health"] = plan.get("strategy_health")
                slot(sym)["plan_allocations"] = plan.get("strategy_allocations")

    for d in decisions:
        for a in d["actions"]:
            sym = str(a.get("symbol") or "")
            if not sym:
                continue
            rec = slot(sym)
            rec["decisions"].append({
                "source": "agent_decisions", "decision_id": d["id"],
                "mode": d.get("mode"), "from_plan": bool(d.get("from_plan")),
                "action": str(a.get("action") or "").lower(),
                "volume": a.get("volume"), "status": a.get("status"),
                "reason": a.get("reason"), "basis": a.get("basis") or []})
            rec["decision_health"] = d["result"].get("strategy_health")
            rec["decision_allocations"] = d["result"].get("strategy_allocations")

    for f in fills:
        sym = str(f.get("symbol") or "")
        slot(sym)["fills"].append({
            "source": "fills", "fill_id": f["fill_id"],
            "direction": "buy" if int(f.get("direction") or 0) == 1 else "sell",
            "volume": f.get("volume"), "price": f.get("price"),
            "fees": round(sum(float(f.get(k) or 0.0) for k in
                              ("commission", "stamp_duty", "transfer_fee")), 2),
            "time": f.get("timestamp")})

    for c in conds:
        sym = str(c.get("symbol") or "")
        slot(sym)["conditions"].append({
            "source": "conditional_orders", "id": c["id"],
            "side": c.get("side"), "type": c.get("cond_type"),
            "value": c.get("cond_value"), "status": c.get("status"),
            "created_at": c.get("created_at"), "filled_at": c.get("filled_at"),
            "filled_price": c.get("filled_price"), "note": c.get("note")})

    for i in intents:
        sym = str(i.get("symbol") or "")
        slot(sym)["intents"].append({
            "source": "rq_intents", "biz_id": i.get("biz_id"),
            "side": i.get("side"), "volume": i.get("volume"),
            "status": i.get("status"), "created_at": i.get("created_at"),
            "rule_ref": i.get("rule_ref")})

    # 未成交/未执行的显式说明（不猜原因，只列证据缺口）
    for rec in timeline.values():
        bought = [f for f in rec["fills"] if f["direction"] == "buy"]
        sold = [f for f in rec["fills"] if f["direction"] == "sell"]
        rec["outcome"] = ("有买入成交" if bought else
                          "有卖出成交" if sold else "当日无成交")
        if not rec["fills"]:
            rec["evidence_gap"] = ("当日无成交；原因见 plans.reason / "
                                   "decisions.status / conditions.status，"
                                   "本报告不推断未记录的原因")

    risk_events = [{"time": o.get("timestamp"), "action": o.get("action"),
                    "ok": bool(o.get("ok")), "result": o.get("result")}
                   for o in ops if str(o.get("action") or "").startswith("risk:")]
    return {
        "ok": True, "date": day, "account_db": str(db),
        "counts": {"plans": len(plans), "decisions": len(decisions),
                   "fills": len(fills), "conditions": len(conds),
                   "intents": len(intents), "op_log": len(ops)},
        "plans": [{"id": p["id"], "plan_date": p["plan_date"],
                   "status": p["status"], "created_at": p["created_at"],
                   "executed_at": p["executed_at"],
                   "summary": (p["plan"] or {}).get("summary"),
                   "health": (p["plan"] or {}).get("strategy_health"),
                   "allocations": (p["plan"] or {}).get("strategy_allocations")}
                  for p in plans],
        "symbols": sorted(timeline),
        "timeline": [timeline[s] for s in sorted(timeline)],
        "risk_events": risk_events,
        "note": ("只读审计；按标的串计划/决策/意图/成交/条件单。"
                 "没有成交不等于看空——以记录里的 reason/status 为准。"),
    }
