"""母侧纸盘 AI 交易员的一次性体检（**不是守护进程**）。

背景：2026-10-23 → 11-19，母侧子项目的 AI 交易员静默停摆三周——
面板一直 200、心跳过期也只显示 stale，直到用户发现"没有计划和交易"。
本脚本回答三个机器可判的问题：

1. cron 任务是否齐全（委托 `scripts/install_mother_agent_cron.sh --check`）；
2. 收盘计划窗口（16:30–18:30）过去后，是否已有**下一交易日**的 pending 计划；
3. 开盘执行窗口（09:30–10:00）过去后，今天的计划是否已 `done`（或有计划可查）。

设计约束：
- 只读：`paper_account.db` 以 `mode=ro` 打开；不调 LLM、不下单、不写表。
- 不依赖 paper_trading 包：只读 `paper_trading/var/trading_calendar.json`
  （缺失时按"周一至周五"回退，与子项目 fail-closed 口径一致）。
- **不自己排程**：路线图明确禁止定时巡检/看门狗；本脚本供人工或发布后检查调用。

用法：
    python scripts/check_paper_agent.py
    python scripts/check_paper_agent.py --json
    python scripts/check_paper_agent.py --now 2026-11-19T18:50   # 补查/测试

退出码：0 = 绿；1 = 有红；2 = 环境问题（库或脚本不可用）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

OPEN_DONE_AT = time(10, 15)    # 09:55 最后一跳 + 执行余量
PLAN_DONE_AT = time(18, 45)    # 18:30 最后一跳 + LLM 余量


def load_trading_days(root: Path) -> set[str]:
    """读子项目的日历缓存；缺失/损坏返回空集（调用方回退周一至周五）。"""
    p = root / "paper_trading" / "var" / "trading_calendar.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {str(x)[:10] for x in raw.get("dates", []) if str(x).strip()}
    except (OSError, ValueError):
        return {}


def is_trading_day(d: date, days: set[str]) -> bool:
    if d.weekday() >= 5:
        return False
    return d.isoformat() in days if days else True


def prev_trading_day(d: date, days: set[str]) -> date:
    cur = d - timedelta(days=1)
    while not is_trading_day(cur, days):
        cur -= timedelta(days=1)
    return cur


def next_trading_day(d: date, days: set[str]) -> date:
    cur = d + timedelta(days=1)
    while not is_trading_day(cur, days):
        cur += timedelta(days=1)
    return cur


def judge(now: datetime, plans: list[dict], *, trading_day: bool,
          prev_td: date, next_td: date,
          cron_ok: bool) -> tuple[bool, list[str]]:
    """纯函数：给定时钟、计划表与 cron 状态，返回 (是否绿, 消息列表)。"""
    msgs: list[str] = []
    ok = True
    if not cron_ok:
        ok = False
        msgs.append("cron 任务不全（跑 scripts/install_mother_agent_cron.sh --check）")

    if not trading_day:
        msgs.append(f"{now.date().isoformat()} 休市，不要求计划/执行")
        return ok, msgs

    today = now.date().isoformat()
    by_date: dict[str, list[dict]] = {}
    for p in plans:
        by_date.setdefault(str(p.get("plan_date"))[:10], []).append(p)

    # 1) 收盘计划窗口之后：下一交易日必须有计划（pending；已被提前执行也算）
    if now.time() >= PLAN_DONE_AT:
        pts = by_date.get(next_td.isoformat(), [])
        if not pts:
            ok = False
            msgs.append(f"计划窗口已过，但没有 {next_td.isoformat()} 的计划"
                        f"（上一交易日 {prev_td.isoformat()} 的 16:30–18:30 窗口没跑成？）")
        else:
            msgs.append(f"{next_td.isoformat()} 计划 #{pts[0].get('id')} "
                        f"状态 {pts[0].get('status')}")
        return ok, msgs

    # 2) 开盘执行窗口之后：今天的计划应已执行；连计划都没有就是上一晚丢了
    if now.time() >= OPEN_DONE_AT:
        tps = by_date.get(today, [])
        if not tps:
            ok = False
            msgs.append(f"开盘窗口已过，但今天（{today}）没有任何计划"
                        f"（上一交易日 {prev_td.isoformat()} 的计划窗口应生成）")
        else:
            st = str(tps[0].get("status"))
            msgs.append(f"今天计划 #{tps[0].get('id')} 状态 {st}")
            if st != "done":
                ok = False
                msgs.append(f"计划 #{tps[0].get('id')} 未执行（status={st}）")
        return ok, msgs

    # 3) 窗口尚未过去：只核对 cron
    return ok, msgs


def read_plans(db: Path, limit: int = 20) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, plan_date, status, created_at, executed_at"
            " FROM agent_plans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def cron_ok(root: Path) -> bool:
    script = root / "scripts" / "install_mother_agent_cron.sh"
    if not script.exists():
        return False
    try:
        return subprocess.run(["bash", str(script), "--check"],
                              cwd=str(root), capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="母侧纸盘 AI 交易员体检（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now", default=None, help="ISO 时刻（补查/测试用）")
    ap.add_argument("--root", default=None, help="仓库根（默认脚本上一级）")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    db = root / "paper_trading" / "paper_account.db"
    if not db.exists():
        print(f"!! 找不到账本：{db}", file=sys.stderr)
        return 2

    try:
        now = datetime.fromisoformat(args.now) if args.now else datetime.now()
    except ValueError:
        print("!! --now 需要 ISO 时刻，如 2026-11-19T18:50", file=sys.stderr)
        return 2

    days = load_trading_days(root)
    plans = read_plans(db)
    ok, msgs = judge(now, plans,
                     trading_day=is_trading_day(now.date(), days),
                     prev_td=prev_trading_day(now.date(), days),
                     next_td=next_trading_day(now.date(), days),
                     cron_ok=cron_ok(root))

    if args.json:
        print(json.dumps({"ok": ok, "now": now.isoformat(timespec="minutes"),
                          "messages": msgs}, ensure_ascii=False))
    else:
        print(f"{'绿' if ok else '红'}  {now.isoformat(timespec='minutes')}")
        for m in msgs:
            print(f"  - {m}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
