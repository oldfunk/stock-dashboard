"""纸盘周度审计（B3，2026-11-28）：计划/成交/拒单/费用/换手。

只读纸盘账本（`mode=ro`），不写、不下单。周报只做复盘，不影响交易。
用法：
    python scripts/paper_weekly_audit.py --start 2026-11-20 --end 2026-11-27
    python scripts/paper_weekly_audit.py --days 7 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.paper_bridge.conn import connect  # noqa: E402
from src.paper_bridge.settings import bridge_paths  # noqa: E402


def parse_plan_json(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def plan_stats(plans: list[dict], fills: list[dict]) -> dict:
    """计划动作 vs 实际成交：买卖动作的兑现率、拒单、持仓动作数。

    每笔成交只能兑现一个计划动作（贪心匹配），避免同一笔成交被多份计划重复计数。
    """
    planned = {"buy": 0, "sell": 0, "hold": 0}
    rejected = []
    actions = []
    for p in plans:
        # superseded 是历史草稿，不参与兑现率；pending 尚未到执行日，也不判未兑现。
        if p.get("status") == "superseded":
            continue
        data = parse_plan_json(p.get("plan_json"))
        for a in (data.get("actions") or []):
            act = str(a.get("action") or "").lower()
            if act in planned:
                planned[act] += 1
            if act in ("buy", "sell") and p.get("status") == "done":
                actions.append({"plan_id": p.get("id"),
                                "plan_date": p.get("plan_date"),
                                "action": act,
                                "symbol": str(a.get("symbol") or "").zfill(6),
                                "volume": a.get("volume")})
        for r in (data.get("actions_rejected") or []):
            rejected.append({
                "plan_id": p.get("id"),
                "plan_date": p.get("plan_date"),
                "symbol": r.get("symbol"),
                "action": r.get("action"),
                "reason": str(r.get("reject_reason")
                              or r.get("reason") or "")[:160],
            })

    used = set()
    matched = 0
    unmatched = []
    for a in actions:
        hit = None
        for i, f in enumerate(fills):
            if i in used:
                continue
            if (str(f.get("symbol")).zfill(6) == a["symbol"]
                    and str(f.get("timestamp") or "")[:10] == a["plan_date"]
                    and (int(f.get("direction") or 0) > 0) == (a["action"] == "buy")):
                hit = i
                break
        if hit is None:
            unmatched.append(a)
        else:
            used.add(hit)
            matched += 1
    trade_actions = len(actions)
    return {
        "plans": len(plans),
        "pending": sum(1 for p in plans if p.get("status") == "pending"),
        "planned": planned,
        "matched": matched,
        "unmatched": unmatched,
        "compliance": (matched / trade_actions) if trade_actions else None,
        "rejected": rejected,
    }


def summarize(plans: list[dict], fills: list[dict], navs: list[dict]) -> dict:
    st = plan_stats(plans, fills)
    fees = sum(float(f.get(k) or 0) for f in fills
               for k in ("commission", "stamp_duty", "transfer_fee"))
    turnover = sum(float(f.get("price") or 0) * float(f.get("volume") or 0)
                   for f in fills)
    first = navs[0] if navs else {}
    last = navs[-1] if navs else {}
    nav0 = float(first.get("total_value") or 0)
    nav1 = float(last.get("total_value") or 0)
    avg_nav = ((nav0 + nav1) / 2) if nav0 and nav1 else None
    return {
        **st,
        "fills": len(fills),
        "buy_fills": sum(1 for f in fills if int(f.get("direction") or 0) > 0),
        "sell_fills": sum(1 for f in fills if int(f.get("direction") or 0) < 0),
        "fees": round(fees, 2),
        "turnover": round(turnover, 2),
        "turnover_pct": round(turnover / avg_nav, 4) if avg_nav else None,
        "nav_start": nav0 or None,
        "nav_end": nav1 or None,
        "nav_pct": ((nav1 / nav0 - 1.0) if nav0 and nav1 else None),
        "nav_start_at": first.get("timestamp"),
        "nav_end_at": last.get("timestamp"),
    }


def _pct(v) -> str:
    return "--" if v is None else f"{v * 100:+.2f}%"


def render(s: dict, start: str, end: str) -> str:
    lines = [f"# 纸盘周度审计（{start} → {end}）", "",
             f"- 计划 {s['plans']} 份（pending {s['pending']} 份）",
             f"- 计划动作：买 {s['planned']['buy']} / 卖 {s['planned']['sell']}"
             f" / 持有 {s['planned']['hold']}",
             f"- 买卖兑现率：{_pct(s['compliance']) if s['compliance'] is not None else '--'}"
             f"（{s['matched']} 笔匹配到当日成交）",
             f"- 实际成交 {s['fills']} 笔（买 {s['buy_fills']} / 卖 {s['sell_fills']}）",
             f"- 费用 {s['fees']:.2f} 元；成交额 {s['turnover']:.2f} 元；"
             f"换手率 {_pct(s['turnover_pct']) if s['turnover_pct'] is not None else '--'}",
             f"- NAV {s['nav_start'] or '--'} → {s['nav_end'] or '--'}"
             f"（{_pct(s['nav_pct'])}）",
             f"  截至 {s['nav_start_at'] or '--'} / {s['nav_end_at'] or '--'}",
             "",
             "## 拒单/被拒动作"]
    if s["rejected"]:
        lines += ["| 计划 | 日期 | 代码 | 动作 | 原因 |", "|---|---|---|---|---|"]
        for r in s["rejected"]:
            lines.append(f"| #{r['plan_id']} | {r['plan_date']} | "
                         f"{r['symbol']} | {r['action']} | {r['reason']} |")
    else:
        lines.append("无。")
    lines += ["", "## 未兑现的买卖动作"]
    if s.get("unmatched"):
        lines += ["| 计划 | 日期 | 代码 | 动作 | 计划数量 |",
                  "|---|---|---|---|---:|"]
        for a in s["unmatched"]:
            lines.append(f"| #{a['plan_id']} | {a['plan_date']} | "
                         f"{a['symbol']} | {a['action']} | {a['volume']} |")
    else:
        lines.append("无。")
    return "\n".join(lines)


def _rows(conn, sql: str, args) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def collect(start: str, end: str) -> tuple[list[dict], list[dict], list[dict]]:
    _, acct = bridge_paths()
    conn = connect(acct)
    if conn is None:
        raise SystemExit("纸盘账户库不存在或不可读")
    try:
        plans = _rows(conn, "SELECT * FROM agent_plans WHERE plan_date BETWEEN ? "
                            "AND ? ORDER BY plan_date, id", (start, end))
        fills = _rows(conn, "SELECT * FROM fills WHERE substr(timestamp,1,10) "
                            "BETWEEN ? AND ? ORDER BY timestamp, fill_id",
                      (start, end))
        navs = _rows(conn, "SELECT * FROM nav_history WHERE substr(timestamp,1,10) "
                           "BETWEEN ? AND ? ORDER BY timestamp, id", (start, end))
    finally:
        conn.close()
    return plans, fills, navs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="纸盘周度审计（只读）")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end)
                           - timedelta(days=max(1, args.days) - 1)).isoformat()
    plans, fills, navs = collect(start, end)
    summary = summarize(plans, fills, navs)
    if args.json:
        print(json.dumps({"start": start, "end": end, "summary": summary,
                          "plans": plans, "fills": fills},
                         ensure_ascii=False, indent=2, default=str))
    else:
        print(render(summary, start, end))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
