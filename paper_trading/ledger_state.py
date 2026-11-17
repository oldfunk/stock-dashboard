"""账本重置后**必须清理的运行态**清单 + 执行器（T62）。

为什么需要这个模块（2026-11-02 周一的现网事故）：旧账冷备后把 `account.cash`
改成新起点，账本「看起来」是新的，但 `risk_state.peak_total_value` 还是旧账的
156,005.23，而新账净值 100,189.96 ⇒ 回撤 35.7% ⇒ `check_drawdown` 每轮为真、
全天零成交。第二天的审计把它读成「迁移失败」，其实是旧账的影子在判新账。
手工清一次能救当天，救不了下一次：状态散在两张 KV 表、意图队列、条件单表和
会话目录里，人脑记不住哪一列该清、哪一列**绝不能**清。

三条不能含糊的规矩：
1. **只清运行态，绝不动钱类**：`account/positions/orders/fills/nav_history/
   t1_freeze/op_log` 一行都不碰，并把前后行数 + cash 写进留痕作为证据
   （将来谁往清单里塞了动钱类的项，钱类对照会当场回滚并报错）；
2. **清之前先把现场抄进 `op_log[ledger:reset-state]`**：删掉的值不可再生，
   没有 before 值的清理等于毁证（切换日那次无留痕的改判已经吃过教训）；
3. **该报的不能装成清掉了**：逾期未解冻的 T+1、被改判 rejected 却仍有成交的
   意图、清单没见过的状态键——这些要人裁决，不是「清一下就好」，一律
   `kind=report`（`--only` 点名字段清不掉），第二次跑还会再报。

清单本体（`TARGETS`）是唯一真相源（纪律 7）：`cli ledger reset-state` 打印它、
`docs/migration-m3.md` 的「重置后必清」表格由它渲染、测试逐条比对文档与代码，
避免文档与代码各说各话。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

ACTION = "ledger:reset-state"

#: 钱类表：本模块**只读不写**，plan/apply 都带它们的行数快照做对照。
MONEY_TABLES = ("account", "positions", "orders", "fills", "nav_history",
                "t1_freeze", "op_log")

KIND_HALT = "halt"          # 旧账的尺子量新账 ⇒ 永久熔断
KIND_GHOST = "ghost"        # 旧账做的决定动新账的钱 ⇒ 凭空下单
KIND_THROTTLE = "throttle"  # 节流/去重标记 ⇒ 最坏漏一次机会或刷屏
KIND_REPORT = "report"      # 只报不清：要人裁决，工具无权替人决定


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    source: str          # 哪张表/哪个目录
    kind: str
    why: str             # 留着会怎样（进留痕与文档；不写「它是什么」）
    action: str          # 清理动作
    required: bool       # False = 默认跳过，需 --only 显式点名


TARGETS: tuple[Target, ...] = (
    Target("risk_peak", "风控峰值净值", "risk_state.peak_total_value", KIND_HALT,
           "峰值是旧账挣出来的，新账第一小时就被回撤熔断挡在门外（周一事故本体）",
           "删除该键，让峰值从新账的第一次观测重新锚定", True),
    Target("risk_daily_baseline", "当日亏损基线", "risk_state.daily_baseline:<日>",
           KIND_HALT,
           "基线是旧账的总资产，日亏比例按旧尺子算 ⇒ 每轮都判熔断；"
           "现网 10-27 那行至今写着 156,005.23",
           "删除全部 daily_baseline:* 键", True),
    Target("rq_intent_pending", "未决意图队列", "rq_intents(status=pending/queued)",
           KIND_GHOST,
           "意图是旧账在某分钟做出的决定，重置后照样被 `rq tick` 消费 ⇒ "
           "新账执行了它没做过的决定",
           "逐条标 rejected（写原因、保留行——删掉就没法解释那天为什么没成交）", True),
    Target("cond_armed", "已挂条件单", "conditional_orders(status=armed)", KIND_GHOST,
           "条件单的价位与金额是按旧账的仓位/现金设的，重置后触发就是拿旧决定下单",
           "逐条标 cancelled（想留的价挂单请在重置后重挂）", True),
    Target("rq_session_state", "引擎会话状态目录", "var/rq_state/{live,<日>}", KIND_GHOST,
           "同日续跑会恢复旧账的 portfolio/universe/挂单，新账直接接上旧持仓继续撮合",
           "整目录改名 <名>.pre-reset-<时刻> 挪开（不删：钱类件的可恢复性优先）", True),
    Target("engine_fired", "规则当日已触发标记", "engine_state.fired:<日>",
           KIND_THROTTLE,
           "规则被记成「今天已经动过」，新账同一分钟再触发会被静默吞掉",
           "删除 fired:* 键", True),
    Target("engine_budget", "日内下单预算", "engine_state.budget:<日>", KIND_THROTTLE,
           "当日订单数/成交额预算已被旧账用掉 ⇒ 新账被判「超出预算」而不下单",
           "删除 budget:* 键", True),
    Target("engine_cooldown", "每标的冷却", "engine_state.cooldown:<标的>",
           KIND_THROTTLE,
           "冷却时间戳来自旧账的成交，新账白白等一段冷却（漏机会，不伤钱）",
           "删除 cooldown:* 键", True),
    Target("engine_trigger_once", "拦截意图的单日一条留痕", "engine_state.trigger-once:*",
           KIND_THROTTLE,
           "开关拦截的流水标记留着 ⇒ 重置后同类拦截不再上面板，看着像「没发生」",
           "删除 trigger-once:* 键（engine_triggers 流水表本身是历史，不动）", True),
    Target("engine_event_throttle", "AI 事件评审节流", "engine_state.event:*",
           KIND_THROTTLE,
           "event:last / event:sym:<标的> / event:budget:<日> 是上一本账的评审配额，"
           "留着会让新账少评审一轮（代价：重置后可能多花一次 LLM 调用）",
           "删除 event:* 键", True),
    Target("t1_unfreeze_overdue", "逾期未解冻的 T+1", "t1_freeze", KIND_REPORT,
           "解冻只发生在 `run` / `agent tick` 的结算段；结算路径整日没跑，"
           "冻结股就一直 available=0（卖不掉）。重置工具无权替结算路径写持仓",
           "报出来，跑一次结算路径或人工裁决", False),
    Target("intent_ledger_conflict", "意图与账本口径冲突", "rq_intents × orders",
           KIND_REPORT,
           "标 rejected/expired 的意图在同源同日仍有成交，而原因又不是幂等/去重 ⇒ "
           "队列与账本讲的是两个故事，先裁决哪边是真的，再谈清理",
           "报出来（现网 10-30 切换日那三行就是这个形状）", False),
    Target("heartbeat_file", "心跳文件", "var/heartbeat.json", KIND_REPORT,
           "心跳记的是数据供给，不是账本状态；删掉反而让「装过才追问」"
           "的分钟采集项假绿",
           "保留（只报当前键与最后时刻）", False),
    Target("unknown_state_keys", "清单没见过的状态键", "risk_state / engine_state",
           KIND_REPORT,
           "新代码往里写了新键而清单没跟上 ⇒ 旧账的运行态被静默留下，"
           "正是这次事故的形状；未知键必须冒出来，不许默默算「清干净了」",
           "报出来，按纪律 7 把键补进本清单", False),
)

_BY_ID = {t.id: t for t in TARGETS}

#: KV 型项的取值口径：{id: (表, 前缀, 全名)}——探测与清理共用，避免两处各写一遍。
_KV_SPEC = {
    "risk_peak": ("risk_state", None, "peak_total_value"),
    "risk_daily_baseline": ("risk_state", "daily_baseline:", None),
    "engine_fired": ("engine_state", "fired:", None),
    "engine_budget": ("engine_state", "budget:", None),
    "engine_cooldown": ("engine_state", "cooldown:", None),
    "engine_trigger_once": ("engine_state", "trigger-once:", None),
    "engine_event_throttle": ("engine_state", "event:", None),
}


def target_ids() -> list[str]:
    return [t.id for t in TARGETS]


def checklist_rows() -> list[dict]:
    """给 CLI 文本与迁移文档用的渲染视图（同一份数据，不各写一遍）。"""
    out = []
    for t in TARGETS:
        d = asdict(t)
        d["default"] = "必清" if t.required else "只报不清"
        out.append(d)
    return out


def selected(*, keep: tuple[str, ...] = (), only: tuple[str, ...] = ()) -> list[Target]:
    """按 CLI 的 --only/--keep 选出生效项；未知 id 直接抛（拼错不许静默少清）。"""
    ids = set(keep or ()) | set(only or ())
    bad = sorted(ids - set(_BY_ID))
    if bad:
        raise ValueError(f"清单里没有这些项：{'、'.join(bad)}"
                         f"（可选：{'、'.join(target_ids())}）")
    chosen = ([_BY_ID[i] for i in only] if only
              else [t for t in TARGETS if t.required])
    return [t for t in chosen if t.id not in set(keep or ())]


# ------------------------------------------------------------------ 只读探测

def _has_table(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone())


def _kv_rows(conn, table: str, prefix: Optional[str] = None,
             exact: Optional[str] = None) -> list[tuple[str, str]]:
    """按前缀/全名挑 KV 表的键。表不存在返回空——旧部署没建过，不算异常。"""
    if not _has_table(conn, table):
        return []
    out = []
    for k, v in conn.execute(f"SELECT key, value FROM {table} ORDER BY key"):  # noqa: S608
        if (exact is not None and k == exact) or \
                (prefix is not None and str(k).startswith(prefix)):
            out.append((k, v))
    return out


def _is_known_kv(table: str, key: str) -> bool:
    for _tid, (tb, prefix, exact) in _KV_SPEC.items():
        if tb != table:
            continue
        if exact is not None and key == exact:
            return True
        if prefix is not None and str(key).startswith(prefix):
            return True
    return False


def _money_snapshot(conn) -> dict:
    """钱类现状：行数 + 账上现金 + 持仓总数——apply 前后必须逐字相同。"""
    snap: dict = {"tables": {}, "cash": None, "position_volume": 0}
    for t in MONEY_TABLES:
        snap["tables"][t] = (int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])  # noqa: S608
                             if _has_table(conn, t) else None)
    if _has_table(conn, "account"):
        row = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
        snap["cash"] = float(row[0]) if row else None
    if _has_table(conn, "positions"):
        snap["position_volume"] = int(conn.execute(
            "SELECT COALESCE(SUM(total_volume),0) FROM positions").fetchone()[0])
    return snap


def _state_dirs(ctx: dict) -> list:
    """当前在用的引擎会话状态目录：`<root>/live` 与 `<root>/<今日>`。

    11-05 之前每跳用 `var/rq_state/<日>`，重置只要挪开当日目录；M3b 改成**固定
    `live` 目录**（连续状态，否则框架天天空账户重启）之后，"当日"这一条就探不到
    真在用的那本状态了——重置工具会报"没有要清的"，而 contaminated 的 portfolio.state
    正躺在 live 里。两个名字都认，存在才算，别的子目录（历史日/归档）一律不碰。
    """
    root = Path(ctx["state_root"])
    out = []
    for name in ("live", ctx["today"]):
        d = root / name
        if d.is_dir() and d not in out:
            out.append(d)
    return out


def _probe(target: Target, conn, ctx: dict) -> dict:
    """返回 `{count, samples[, note]}`；samples 只带够人眼判断的量。"""
    tid = target.id
    if tid in _KV_SPEC:
        table, prefix, exact = _KV_SPEC[tid]
        rows = _kv_rows(conn, table, prefix, exact)
        return {"count": len(rows),
                "samples": [f"{k}={str(v)[:48]}" for k, v in rows[:6]]}
    if tid == "rq_intent_pending":
        if not _has_table(conn, "rq_intents"):
            return {"count": 0, "samples": []}
        rows = conn.execute(
            "SELECT biz_id, symbol, side, volume, status FROM rq_intents"
            " WHERE status IN ('pending','queued') ORDER BY created_at").fetchall()
        return {"count": len(rows),
                "samples": [f"{r[0]} {r[1]} {r[2]} {r[3]} [{r[4]}]" for r in rows[:6]]}
    if tid == "cond_armed":
        if not _has_table(conn, "conditional_orders"):
            return {"count": 0, "samples": []}
        rows = conn.execute(
            "SELECT id, symbol, side, cond_type, cond_value, expiry"
            " FROM conditional_orders WHERE status='armed' ORDER BY id").fetchall()
        return {"count": len(rows),
                "samples": [f"#{r[0]} {r[1]} {r[2]} {r[3]}={r[4]} 到期{r[5]}"
                            for r in rows[:6]]}
    if tid == "rq_session_state":
        dirs = _state_dirs(ctx)
        if not dirs:
            return {"count": 0, "samples": [],
                    "note": f"{Path(ctx['state_root'])} 下没有 live/<日> 状态目录"}
        return {"count": len(dirs),
                "samples": [f"{d}（{len(list(d.iterdir()))} 个文件）" for d in dirs]}
    if tid == "t1_unfreeze_overdue":
        if not _has_table(conn, "t1_freeze"):
            return {"count": 0, "samples": []}
        rows = conn.execute(
            "SELECT symbol, volume, freeze_date, unfreeze_date FROM t1_freeze"
            " WHERE is_unfrozen=0 AND unfreeze_date < ? ORDER BY id",
            (ctx["today"],)).fetchall()
        return {"count": len(rows),
                "samples": [f"{r[0]} {r[1]}股 冻结于{r[2]} 应于{r[3]}解冻"
                            "（结算路径没跑到）" for r in rows[:6]]}
    if tid == "intent_ledger_conflict":
        if not (_has_table(conn, "rq_intents") and _has_table(conn, "orders")):
            return {"count": 0, "samples": []}
        # 幂等/去重挡下的意图本来就该与成交共存，不算冲突；冲突是「无因改判」。
        rows = conn.execute(
            "SELECT i.biz_id, i.status, o.order_id, o.symbol, o.direction,"
            " o.volume, o.filled_price FROM rq_intents i JOIN orders o"
            " ON substr(o.symbol,1,6) = substr(i.symbol,1,6)"
            " AND o.direction = CASE WHEN upper(i.side)='BUY' THEN 1 ELSE -1 END"
            " AND o.volume = i.volume"
            " AND substr(o.created_at,1,10) = substr(i.created_at,1,10)"
            " WHERE i.status IN ('rejected','expired') AND o.filled_volume > 0"
            " AND (i.result IS NULL OR (i.result NOT LIKE '%去重%'"
            "  AND i.result NOT LIKE '%幂等%' AND i.result NOT LIKE '%duplicate%'))"
            " ORDER BY o.order_id, i.biz_id").fetchall()
        return {"count": len(rows),
                "samples": [f"{r[0]} 标 {r[1]} 但成交 #{r[2]} {r[3]}"
                            f" {'BUY' if int(r[4]) == 1 else 'SELL'} {r[5]}@{r[6]} 仍在账"
                            for r in rows[:6]]}
    if tid == "heartbeat_file":
        p = Path(ctx["heartbeat"])
        if not p.exists():
            return {"count": 0, "samples": [f"{p} 不存在"], "note": "保留（与账本无关）"}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:                       # noqa: BLE001 - 读坏也要报出来
            return {"count": 0, "samples": [f"{p} 读不出：{type(exc).__name__}"]}
        keys = "，".join(f"{k}@{str((v or {}).get('ts'))[:19]}"
                         for k, v in sorted(data.items()))
        return {"count": 0, "samples": [keys or f"{p} 空"], "note": "保留（与账本无关）"}
    if tid == "unknown_state_keys":
        unknown: list[str] = []
        for table in ("risk_state", "engine_state"):
            if not _has_table(conn, table):
                continue
            for k, in conn.execute(f"SELECT key FROM {table} ORDER BY key"):  # noqa: S608
                if not _is_known_kv(table, k):
                    unknown.append(f"{table}.{k}")
        return {"count": len(unknown), "samples": unknown[:8]}
    raise KeyError(f"没有探测实现：{tid}")


def _clear(target: Target, conn, ctx: dict) -> dict:
    """执行清理；返回 `{cleared, before[, after]}`。`before` 必须够长，事后能复原口径。"""
    tid = target.id
    if tid in _KV_SPEC:
        table, prefix, exact = _KV_SPEC[tid]
        rows = _kv_rows(conn, table, prefix, exact)
        for k, _ in rows:
            conn.execute(f"DELETE FROM {table} WHERE key = ?", (k,))  # noqa: S608
        return {"cleared": len(rows), "before": [{"key": k, "value": v} for k, v in rows]}
    if tid == "rq_intent_pending":
        rows = [dict(r) for r in conn.execute(
            "SELECT biz_id, symbol, side, volume, status, created_at FROM rq_intents"
            " WHERE status IN ('pending','queued') ORDER BY created_at")]
        for r in rows:
            conn.execute(
                "UPDATE rq_intents SET status='rejected', result=? WHERE biz_id=?",
                (json.dumps({"reason": f"账本重置：{ctx['reason']}",
                             "reset_on": ctx["now"]}, ensure_ascii=False), r["biz_id"]))
        return {"cleared": len(rows), "before": rows, "after": "rejected"}
    if tid == "cond_armed":
        rows = [dict(r) for r in conn.execute(
            "SELECT id, symbol, side, cond_type, cond_value, expiry, volume, value"
            " FROM conditional_orders WHERE status='armed' ORDER BY id")]
        for r in rows:
            conn.execute(
                "UPDATE conditional_orders SET status='cancelled', detail=?"
                " WHERE id=? AND status='armed'",
                (json.dumps({"why": f"账本重置：{ctx['reason']}",
                             "cancelled_on": ctx["now"]}, ensure_ascii=False), r["id"]))
        return {"cleared": len(rows), "before": rows, "after": "cancelled"}
    if tid == "rq_session_state":
        moved = []
        for src in _state_dirs(ctx):
            dst = src.with_name(f"{src.name}.pre-reset-{ctx['stamp']}")
            if dst.exists():
                raise RuntimeError(f"改名目标已存在，拒绝覆盖：{dst}（给 --stamp 换个值）")
            src.rename(dst)
            moved.append([str(src), str(dst)])
        if not moved:
            return {"cleared": 0, "before": []}
        for s, d in moved:
            ctx["moved"].append([s, d])
        return {"cleared": len(moved), "before": [s for s, _ in moved],
                "after": [d for _, d in moved]}
    raise KeyError(f"只报不清的项不能执行清理：{tid}")


def _default_heartbeat(account_db: Path) -> str:
    for cand in (Path("var/heartbeat.json"),
                 account_db.parent / "var" / "heartbeat.json",
                 account_db.with_name("heartbeat.json")):
        if cand.exists():
            return str(cand)
    return str(Path("var/heartbeat.json"))


def _ctx(account_db: Path, today: Optional[str], state_root: str,
         heartbeat: Optional[str], reason: str, stamp: Optional[str],
         now: Optional[datetime]) -> dict:
    tn = now or datetime.now()
    return {"today": today or tn.date().isoformat(),
            "now": tn.isoformat(timespec="seconds"),
            "stamp": stamp or tn.strftime("%Y%m%d-%H%M%S"),
            "state_root": state_root,
            "heartbeat": heartbeat or _default_heartbeat(account_db),
            "reason": reason, "moved": []}


def plan(account_db, *, today: Optional[str] = None, state_root: str = "var/rq_state",
         heartbeat: Optional[str] = None, keep: tuple[str, ...] = (),
         only: tuple[str, ...] = (), now: Optional[datetime] = None) -> dict:
    """只读体检：列出每一项现状 + 钱类快照，不写任何东西（`--yes` 之前先看这个）。"""
    p = Path(str(account_db))
    ctx = _ctx(p, today, state_root, heartbeat, "", None, now)
    eff = {t.id for t in selected(keep=keep, only=only)}
    items = []
    with closing(sqlite3.connect(f"file:{p}?mode=ro", uri=True)) as conn:
        for t in TARGETS:
            info = _probe(t, conn, ctx)
            items.append({**asdict(t), "count": info["count"],
                          "samples": info.get("samples", []),
                          "note": info.get("note", ""),
                          "will_clear": bool(t.required and t.id in eff
                                             and t.kind != KIND_REPORT
                                             and info["count"])})
        money = _money_snapshot(conn)
    return {"account_db": str(p), "today": ctx["today"], "state_root": state_root,
            "heartbeat": ctx["heartbeat"], "items": items, "money_before": money,
            "pending": [i["id"] for i in items if i["will_clear"]],
            "red_reports": [i["id"] for i in items
                            if i["count"] and i["kind"] == KIND_REPORT]}


def apply_reset(account_db, broker, *, reason: str, today: Optional[str] = None,
                state_root: str = "var/rq_state", heartbeat: Optional[str] = None,
                keep: tuple[str, ...] = (), only: tuple[str, ...] = (),
                stamp: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """按清单清运行态并留痕（写库；默认调用方已在 CLI 里要过 `--yes`）。

    写入顺序是有意的：**先挪会话目录，再清库**。目录里是旧账的 portfolio 快照，
    先把这条路堵死，后面即使崩了也不会有「旧状态续跑新账」的窗口；清库失败
    则把目录改名回来，不留在半截状态。
    """
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError('ledger reset-state 会改运行态，必须给 --reason'
                         '（写清「哪本账、为什么重置」，留痕要能审计）')
    p = Path(str(account_db))
    ctx = _ctx(p, today, state_root, heartbeat, reason, stamp, now)
    targets = selected(keep=keep, only=only)
    clear_targets = [t for t in targets if t.kind != KIND_REPORT]
    with closing(sqlite3.connect(str(p), timeout=15)) as conn:
        conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row      # _clear 里要 dict(row)，默认工厂给的是元组
        money_before = _money_snapshot(conn)
        with closing(sqlite3.connect(f"file:{p}?mode=ro", uri=True)) as ro:
            pre = {t.id: _probe(t, ro, ctx) for t in targets}
        results: dict = {}
        try:
            for t in clear_targets:
                if pre[t.id]["count"] == 0 and t.id != "rq_session_state":
                    results[t.id] = {"cleared": 0, "before": []}
                    continue
                results[t.id] = _clear(t, conn, ctx)
            money_after = _money_snapshot(conn)
            if (money_after["tables"] != money_before["tables"]
                    or money_after["cash"] != money_before["cash"]
                    or money_after["position_volume"] != money_before["position_volume"]):
                raise RuntimeError(
                    f"钱类现状被改动，本次清理回滚：before={money_before}"
                    f" after={money_after}（reset-state 的契约是只清运行态；"
                    "先查是不是清单里混进了动钱类的项）")
            conn.commit()
        except Exception:
            for src, dst in ctx["moved"]:
                Path(dst).rename(src)      # 目录挪回来了；DB 事务没提交
            raise
        cleared = sum(r.get("cleared", 0) for r in results.values())
        reports = {t.id: _probe(t, conn, ctx) for t in TARGETS if t.kind == KIND_REPORT}
        trail = {"reason": reason, "today": ctx["today"],
                 "state_root": state_root, "heartbeat": ctx["heartbeat"],
                 "targets": [t.id for t in targets],
                 "kept": list(keep or ()), "only": list(only or ()),
                 "cleared_total": cleared,
                 "cleared": {k: {"count": v.get("cleared", 0),
                                 "before": v.get("before", [])[:40],
                                 "after": v.get("after", "")}
                             for k, v in results.items()},
                 "reports": {k: {"count": v["count"], "samples": v["samples"]}
                             for k, v in reports.items()},
                 "state_dirs_moved": ctx["moved"],
                 "money_untouched": money_after}
    if cleared == 0 and not ctx["moved"]:
        return {"applied": False, "no_op": True, "reason": reason, "cleared": 0,
                "reports": trail["reports"], "money": money_after,
                "note": "清单里没有可清的运行态（重复跑 reset-state 不写留痕，不刷屏）"}
    broker.log_operation(ACTION, {"action": ACTION, "stamp": ctx["stamp"],
                                  "reason": reason, "targets": trail["targets"]},
                         True, trail, money_after["cash"], None)
    return {"applied": True, "no_op": False, "reason": reason, "cleared": cleared,
            "cleared_by_target": {k: v.get("cleared", 0) for k, v in results.items()},
            "reports": trail["reports"], "state_dirs_moved": ctx["moved"],
            "money": money_after, "trail": trail,
            "note": "运行态已清并留痕 op_log[ledger:reset-state]"}
