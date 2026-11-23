"""每日核对（T80·M4-②）：把 T77 的观察清单变成一条只读命令 + 一个退出码。

为什么要有这条：M4 的验收口径是"连续 5 个交易日无未归因异常"，而"我每天看一眼
面板"不会留下可比较的证据。核对项必须**跑一次出一个码**，红就非零，
这样 CI / cron / 人都能读同一个结论，而不是各自解释截图。

三条 rq 侧核对（`m3 verify` 那 12 项之外的、只看 rqalpha 执行面）：
① `init_align`  框架账户与账本是否同源（11-05 P0 的日常体检）；
② `carry_over`  Q6 顺延的意图今天有没有落地（不能只顺延不兑现）；
③ `intent_chain` 当日新意图全链闭环（入队→会话→落账→标记）。

2026-11-17/18 两次事故后追加三项（都是"日志里有真相、门禁看不见"的形状）：
④ `cond_loop`    已成交的条件单必须 filled（11-17 的 queued_rq 事故）；
⑤ `engine_crash` engine.log 当日 `crashed` 扫描（11-17 刷 250 次仍全绿）；
⑥ `tick_stall`   rq_tick.log 当日 `InitPositionError` / `IdleSpin` 扫描
                （11-18 每跳 0 退出但全天 0 成交）。

全部只读：开库一律 `mode=ro`，不写 `op_log`，不碰状态目录。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from paper_trading.utils.m3_verify import item


def _ro(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def latest_evidence(state_dir, today: str) -> tuple[Optional[dict], str]:
    """读当天的会话留痕：优先 `<state_dir>/evidence_<today>.json`（最新一份
    别名），没有就读当天带时刻的 `evidence_<today>_<HHMMSS>.json` 里最新的一份。

    2026-11-19 起每跳一份证据文件（T94）：一天可能有多场会话，旧实现后一场
    覆盖前一场。读取端保持"拿最近一份"的语义，同时不再在别名缺失时假装
    "今天没有留痕"——那会把一次真实会话的证据降级成不存在。
    """
    d = Path(str(state_dir))
    f = d / f"evidence_{today}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")), f.name
        except Exception as exc:                    # noqa: BLE001 - 坏了要说坏在哪
            return None, f"{f.name} 解析失败：{type(exc).__name__}: {exc}"
    cand = sorted(d.glob(f"evidence_{today}_*.json")) if d.is_dir() else []
    if cand:
        latest = cand[-1]
        try:
            return json.loads(latest.read_text(encoding="utf-8")), latest.name
        except Exception as exc:                    # noqa: BLE001
            return None, f"{latest.name} 解析失败：{type(exc).__name__}: {exc}"
    return None, f"今日无会话留痕（{f.name} 不存在）"


def today_buys(account_db, today: str) -> set:
    """当日**买成交**的标的（order_book_id）。期初留痕里不可能有它们。"""
    from paper_trading.integration.rq_engine.store import to_order_book_id

    with contextlib.closing(_ro(account_db)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                         " AND name='fills'").fetchone():
            return set()
        return {to_order_book_id(r["symbol"]) for r in c.execute(
            "SELECT DISTINCT symbol FROM fills WHERE substr(timestamp,1,10)=?"
            " AND direction=1", (today,))}


def check_init_align(state_dir, account_db, today: str, *,
                     work_expected: Optional[bool] = None) -> dict:
    """框架账户必须与账本同源（T73 的日常化）。

    `work_expected=False`（队列本来就空、没开会话）⇒ 没有留痕是正常的，判 skip；
    其余情况下"没留痕/被吞/比账本少仓"都是红。

    **同期比同期**（T85 彩排照出来的假红）：留痕是会话期初的快照，账本是"此刻"。
    当天卖光两只仓之后 `framework=8 > 账本 6` 是**正常**的，拿它判多仓就会天天
    钱类红 ⇒ 门禁成了永久非零的壁纸。所以多/少仓一律用留痕里同一时刻的
    `positions_landed.checked`（期初应有）与 `framework`（期初实有）比；
    当前账本只用来查"期初该有的仓没进框架"，且豁免当日买成交新建的仓。
    """
    ev, src = latest_evidence(state_dir, today)
    if ev is None:
        if work_expected is False:
            return item("init_align", "框架账户与账本同源", "skip", src)
        return item("init_align", "框架账户与账本同源", "red",
                    f"{src}——本判非零（会话没跑成/留痕丢了，持仓对齐无从证明）",
                    money=True, on_red="查 rq tick 日志与 cron")
    align = ev.get("init_align") or {}
    landed = ev.get("positions_landed") or {}
    frozen = ev.get("frozen_align") or {}
    if align.get("dropped_silently"):
        return item("init_align", "框架账户与账本同源", "red",
                    f"期初持仓被框架吞掉：{align['dropped_silently']}",
                    money=True, on_red="形态/解析层又变了（见 report-t73 §2）")
    uns = align.get("unsupported") or []
    uns_syms = {str(u.get("symbol")) for u in uns}
    ledger = ledger_positions(account_db)
    checked_n, fw_n = landed.get("checked"), landed.get("framework")
    # 少仓：期初实有 < 期初应有（同一时刻的两个数，与当天成交无关）
    if isinstance(checked_n, int) and isinstance(fw_n, int) and fw_n < checked_n:
        return item("init_align", "框架账户与账本同源", "red",
                    f"框架期初持仓 {fw_n} 只 < 账本应有 {checked_n} 只（留痕 {src}）",
                    money=True,
                    on_red="先停 cron，`ledger reset-state --only rq_session_state`")
    # 框架标的表里本就没有的合约（开源 bundle 无北交所 `.BJ`）不是"缺仓"——
    # 它被 `reconcile_init_positions` 如实剔除过，runner 侧会以
    # INSTRUMENT_UNAVAILABLE 给意图定局。豁免只认这份剔除留痕，不认"我没查到"。
    kept_syms = {str(k) for k, _ in align.get("kept") or []}
    bought = today_buys(account_db, today)
    missing = [{"symbol": ob, "ledger": qty} for ob, qty in sorted(ledger.items())
               if qty > 0 and ob not in kept_syms and ob not in uns_syms
               and ob not in bought]
    if missing:
        return item("init_align", "框架账户与账本同源", "red",
                    f"账本有仓而未进框架：{missing[:5]}（留痕 {src}）",
                    money=True,
                    on_red="先停 cron，`ledger reset-state --only rq_session_state`")
    # 反方向也要查（T81 P2-3）：框架多出账本没有的仓 ⇒ 按不存在的持仓卖出、
    # 按虚高市值算风险。留痕里有逐只 `extra` 就用它；旧留痕只有计数，
    # 那就用**同期**的"框架只数 > 期初应有只数"兜底（不是与此刻的账本比）。
    extra = landed.get("extra")
    if extra:
        return item("init_align", "框架账户与账本同源", "red",
                    f"框架多出账本没有的仓：{extra[:5]}（留痕 {src}）",
                    money=True,
                    on_red="先停 cron，`ledger reset-state --only rq_session_state`")
    if extra is None and isinstance(checked_n, int) and isinstance(fw_n, int) \
            and fw_n > checked_n:
        return item("init_align", "框架账户与账本同源", "red",
                    f"框架期初持仓 {fw_n} 只 > 账本应有 {checked_n} 只（留痕 {src}"
                    f" 无逐只 extra 字段，按同期计数判多仓）", money=True,
                    on_red="先停 cron，`ledger reset-state --only rq_session_state`")
    bits = [f"框架持仓 {landed.get('framework', '?')} 只/账本 {len(ledger)} 只",
            f"T+1 冻结留痕 {len(frozen.get('applied') or [])} 条"]
    if uns:
        bits.append(f"框架无此合约 {len(uns)} 只（{[u['symbol'] for u in uns][:3]}）")
    return item("init_align", "框架账户与账本同源", "green", "；".join(bits))


def ledger_positions(account_db) -> dict:
    """只读账本持仓（`order_book_id → total_volume`）。读不出就抛（核对不许猜）。"""
    from paper_trading.integration.rq_engine.store import to_order_book_id

    with contextlib.closing(_ro(account_db)) as c:
        return {to_order_book_id(r["symbol"]): int(r["total_volume"])
                for r in c.execute("SELECT symbol, total_volume FROM positions"
                                   " WHERE total_volume > 0")}


def check_intent_chain(account_db, today: str, *, executor: str = "rqalpha") -> dict:
    """当日新意图全链闭环：每条都得有结论，且 `executed` 必须有账本成交为证。

    一致性断言（T81 P2-1，此前 docstring 承诺了但代码没做）：
    * 认领成交的意图：`result.order_id` 必须指向账本**当日 filled** 的那一笔，
      且标的/方向/数量相符；
    * 幂等命中的意图（`reason` 含"幂等命中"）：当日账本必须真有同款成交；
    * 两者都给不出 ⇒ **钱类红**：意图表说成交了、账本没有，就是假账。
    反方向：当日 filled 里没有任何意图认领的 ⇒ warn（条件单/旧线也会成交，
    不能一律当假账，但必须说得出口，不许静默）。
    """
    from paper_trading.integration.rq_engine.queue import intent_key

    with contextlib.closing(_ro(account_db)) as c:
        has_intents = bool(c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rq_intents'"
        ).fetchone())
        rows = [dict(r) for r in c.execute(
            "SELECT biz_id, symbol, side, volume, status, created_at, result"
            " FROM rq_intents WHERE substr(created_at,1,10)=?", (today,))] \
            if has_intents else []
        if not rows:
            return item("intent_chain", "当日意图全链", "skip",
                        f"{today} 没有新意图（executor={executor}）")
        has_orders = bool(c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='orders'"
        ).fetchone())
        orders = [dict(r) for r in c.execute(
            "SELECT order_id, symbol, direction, volume, status FROM orders"
            " WHERE substr(created_at,1,10)=? AND status='filled'", (today,))] \
            if has_orders else []
        # 认领关系要跨日看：昨天盘后决定、今天顺延执行的意图，它的 created_at
        # 是昨天，不在 rows 里，但那笔成交确实是它认领的。
        claimed, idem_keys = set(), set()
        for r in (c.execute("SELECT symbol, side, volume, result FROM rq_intents"
                            " WHERE status='executed'") if has_intents else []):
            try:
                res = json.loads(r["result"] or "{}") or {}
            except Exception:                # noqa: BLE001 - 坏了下面按"没认领"算
                res = {}
            oid = res.get("order_id")
            if oid is not None:
                claimed.add(int(oid))
            elif "幂等命中" in str(res.get("reason") or ""):
                # 幂等命中不带 order_id：它认领的是"当日同款"那笔成交
                idem_keys.add(intent_key(r["symbol"], r["side"], r["volume"]))
    by_oid = {int(o["order_id"]): o for o in orders}
    filled_keys = {intent_key(o["symbol"],
                              "BUY" if int(o["direction"]) == 1 else "SELL",
                              o["volume"]) for o in orders}
    dist: dict = {}
    for r in rows:
        dist[r["status"]] = dist.get(r["status"], 0) + 1
    detail = f"当日 {len(rows)} 条：{dist}；账本当日 filled={len(orders)}"

    bad = []
    for r in rows:
        if r["status"] != "executed":
            continue
        try:
            res = json.loads(r["result"] or "{}") or {}
        except Exception:                    # noqa: BLE001
            res = {}
        oid = res.get("order_id")
        if oid is not None:
            o = by_oid.get(int(oid))
            want = (str(r["symbol"]).split(".")[0][:6].zfill(6),
                    str(r["side"]).upper(), int(r["volume"]))
            got = (str(o["symbol"]).split(".")[0][:6].zfill(6),
                   "BUY" if int(o["direction"]) == 1 else "SELL",
                   int(o["volume"])) if o else None
            if o is None or got != want:
                bad.append({"biz_id": r["biz_id"], "order_id": oid,
                            "why": "账本当日无此 filled 成交" if o is None
                                   else f"成交与意图不符（账本 {got}）"})
        elif "幂等命中" in str(res.get("reason") or ""):
            if intent_key(r["symbol"], r["side"], r["volume"]) not in filled_keys:
                bad.append({"biz_id": r["biz_id"],
                            "why": "标了幂等命中，但账本当日没有同款成交"})
        else:
            bad.append({"biz_id": r["biz_id"],
                        "why": f"executed 却无成交凭据（result={str(res)[:80]}）"})
    if bad:
        return item("intent_chain", "当日意图全链", "red",
                    f"{detail}——executed 与账本对不上 {len(bad)} 条：{bad[:3]}",
                    money=True, on_red="停 cron 归因：意图表与账本必须同源")
    unclaimed = [int(o["order_id"]) for o in orders
                 if int(o["order_id"]) not in claimed
                 and intent_key(o["symbol"],
                                "BUY" if int(o["direction"]) == 1 else "SELL",
                                o["volume"]) not in idem_keys]
    open_ = [r for r in rows if r["status"] in ("pending", "queued")]
    if open_:
        return item("intent_chain", "当日意图全链", "warn",
                    f"{detail}——仍未定局 {len(open_)} 条"
                    f"（{[o['biz_id'] for o in open_][:3]}）：收盘后还在等就是漏环节",
                    on_red="归因")
    if unclaimed:
        return item("intent_chain", "当日意图全链", "warn",
                    f"{detail}——{len(unclaimed)} 笔成交无意图认领"
                    f"（order_id {unclaimed[:5]}）：条件单/旧线成交要归因，不许静默",
                    on_red="归因")
    return item("intent_chain", "当日意图全链", "green",
                detail + f"；executed 逐笔有账本凭据，成交全部有认领")


def check_carry_over(account_db, today: str, *, now: Optional[datetime] = None,
                     executor: str = "rqalpha") -> dict:
    """Q6 顺延的意图必须**兑现**：顺延过来还挂着（或干脆没执行）要说得出口。

    顺延时效只有今天一天：明天再核对时，昨天的顺延意图必须已是
    executed/rejected，不能变成第二条"静静挂着的 queued"。
    """
    if executor != "rqalpha":
        return item("carry_over", "盘后意图顺延落地", "skip",
                    f"executor={executor}，不消费 rq 队列")
    with contextlib.closing(_ro(account_db)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                         " AND name='rq_intents'").fetchone():
            return item("carry_over", "盘后意图顺延落地", "skip", "还没有意图表")
        rows = [dict(r) for r in c.execute(
            "SELECT biz_id, symbol, side, volume, status, created_at"
            " FROM rq_intents WHERE substr(created_at,1,10) < ?"
            " AND status IN ('pending','queued','executed','rejected')", (today,))]
    from paper_trading.integration.rq_engine.queue import is_carried

    carried = [r for r in rows if is_carried(r["created_at"], today)]
    if not carried:
        stale = [r for r in rows if r["status"] in ("pending", "queued")]
        if stale:
            # T81 P2-2：过期未决还挂着 = `expire_stale` 漏跑（或会话根本没起）。
            # 此前这里一律 green，等于把"没人清理队列"报成健康。
            return item("carry_over", "盘后意图顺延落地", "warn",
                        f"今日无顺延意图，但昨日及以前仍有 {len(stale)} 条未决挂着"
                        f"（{[s['biz_id'] for s in stale][:3]}）——应已被过期处理",
                        on_red="查 rq tick 是否跑成、expire_stale 是否被跳过")
        return item("carry_over", "盘后意图顺延落地", "green",
                    f"今日无顺延意图（昨日及以前未决 0 条，过期处理已生效）")
    open_ = [r for r in carried if r["status"] in ("pending", "queued")]
    samples = [(r["symbol"], r["side"], r["volume"], r["status"]) for r in carried][:5]
    detail = f"顺延 {len(carried)} 条：{samples}"
    if open_:
        return item("carry_over", "盘后意图顺延落地", "warn",
                    f"{detail}——仍未定局 {len(open_)} 条（本跳应已给出成交/拒单结论）",
                    on_red="查会话是否跑成")
    return item("carry_over", "盘后意图顺延落地", "green", detail)


def check_cond_loop(account_db, today: str, *, executor: str = "rqalpha") -> dict:
    """条件单闭环：今天触发入队的条件单，成交后状态必须落定（2026-11-17 新增）。

    队列模式下"触发"只是入队（`queued_rq`），成交要等 `rq tick`。11-17 生产
    照出：条件单 #98 触发后 `mark('queued_rq')` 抛非法状态 ⇒ 状态停在 armed ⇒
    引擎每跳重复入队（次日会再买一次）、后续 armed 条件同跳被跳过、而
    `cond_chain`（只认 triggered/executed）看不见。判据：今日 `rule_ref=cond:<id>`
    的意图已 executed ⇒ 该条件单必须 `filled`。
    """
    if executor != "rqalpha":
        return item("cond_loop", "条件单闭环", "skip",
                    f"executor={executor}，条件单走直撮路径")
    with contextlib.closing(_ro(account_db)) as c:
        for t in ("rq_intents", "conditional_orders"):
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                             " AND name=?", (t,)).fetchone():
                return item("cond_loop", "条件单闭环", "skip", f"还没有 {t} 表")
        rows = [dict(r) for r in c.execute(
            "SELECT biz_id, rule_ref, status FROM rq_intents"
            " WHERE substr(created_at,1,10)=? AND rule_ref LIKE 'cond:%'", (today,))]
        # T92（2026-11-20）：僵尸条件单——触发后入了队列、意图已过期，条件单
        # 却一直停在 queued_rq。今天没有新触发时也必须报，不能报"无条件触发"绿。
        cutoff = (datetime.fromisoformat(today) - timedelta(days=3)).date().isoformat()
        zombies = [dict(r) for r in c.execute(
            "SELECT id, symbol, created_at FROM conditional_orders"
            " WHERE status='queued_rq' AND substr(created_at,1,10) < ?"
            " ORDER BY id", (cutoff,))]
        if not rows:
            if zombies:
                return item(
                    "cond_loop", "条件单闭环", "warn",
                    f"{today} 无条件触发，但有 {len(zombies)} 条 queued_rq 超过 3 天"
                    f"未落定：{[z['id'] for z in zombies][:6]}——"
                    "意图多半已跨日过期而条件单没跟着收口",
                    on_red="查 expire_stale 是否落定条件单（T92）/expire_due 是否被跳过")
            return item("cond_loop", "条件单闭环", "green", f"{today} 无条件触发")
        ids = set()
        for r in rows:
            cid = str(r["rule_ref"])[5:].strip()
            if cid.isdigit():
                ids.add(int(cid))
        if not ids:
            return item("cond_loop", "条件单闭环", "skip",
                        f"{today} 有 {len(rows)} 条 cond 意图但 rule_ref 解析不出 id")
        st = {int(r["id"]): str(r["status"]) for r in c.execute(
            "SELECT id, status FROM conditional_orders WHERE id IN (%s)"
            % ",".join("?" * len(ids)), tuple(ids))}
    executed = {str(r["rule_ref"])[5:].strip() for r in rows
                if r["status"] == "executed"}
    stuck = sorted(int(i) for i in executed if st.get(int(i)) != "filled")
    detail = (f"触发 {sorted(ids)}｜意图 {len(rows)} 条"
              f"（executed {sum(1 for r in rows if r['status'] == 'executed')}）")
    if stuck:
        return item("cond_loop", "条件单闭环", "red",
                    f"{detail}——已成交的条件单仍非 filled：{stuck}"
                    f"（会在下一交易日重复触发买入；面板也会一直显示待触发）",
                    on_red="查 rq 队列闭环（queue.apply_outcomes）与条件状态白名单")
    if zombies:
        return item("cond_loop", "条件单闭环", "warn",
                    f"{detail}；另有 {len(zombies)} 条 queued_rq 超过 3 天未落定："
                    f"{[z['id'] for z in zombies][:6]}",
                    on_red="查 expire_stale 是否落定条件单（T92）/expire_due 是否被跳过")
    return item("cond_loop", "条件单闭环", "green", detail)


def check_engine_crashes(account_db, today: str, *, limit: int = 5) -> dict:
    """引擎日志崩溃扫描：`engine/conds crashed` 这类异常不得静默（warn）。

    11-17 的 `非法状态：queued_rq` 在 engine.log 里刷了 250 次，而 daily-check
    全绿——log 里的崩溃不会自己变成红/黄。只扫当天、只报计数与首因，
    避免历史日志把每天报成黄。
    """
    log = Path(str(account_db)).resolve().parent / "engine.log"
    if not log.exists():
        return item("engine_crash", "引擎崩溃扫描", "skip", f"{log.name} 不存在")
    hits: list[str] = []
    try:
        with log.open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                if "crashed" not in ln or f"[{today}" not in ln:
                    continue
                hits.append(ln.strip()[:220])
                if len(hits) >= limit:
                    break
    except OSError as exc:                      # noqa: BLE001 - 读不了要说出来
        return item("engine_crash", "引擎崩溃扫描", "warn",
                    f"读 {log.name} 失败：{type(exc).__name__}: {exc}")
    if hits:
        return item("engine_crash", "引擎崩溃扫描", "warn",
                    f"{today} 引擎崩溃 ≥{len(hits)} 次：{hits[0]}",
                    on_red="查该异常是否已修/是否仍在刷（崩溃跳会跳过后续检查项）")
    return item("engine_crash", "引擎崩溃扫描", "green", f"{today} 无崩溃记录")


def check_tick_stall(account_db, today: str, *, sample: int = 3) -> dict:
    """rq tick 会话拒开/空转扫描（2026-11-18 事故派生，T93）。

    11-18 每跳都以 0 退出、心跳正常，而 `InitPositionError` 在 rq_tick.log
    刷了 142 次、全天 0 成交——`engine_crash` 只扫 engine.log 且只认
    `crashed` 关键词，这类"活着但没干活"完全看不见。判据：当天日志里出现
    `InitPositionError`（框架账户与账本脱钩）或 `IdleSpin:`（会话空转）
    ⇒ warn（不是红：钱没动，但执行能力已经没了，必须在收盘报告里出现）。

    日志是追加写的：按行首日期切换"当前业务日"，只认 today 段——
    traceback 的后续行没有时间戳，跟着它所属的日期段走。
    """
    log = Path(str(account_db)).resolve().parent / "rq_tick.log"
    if not log.exists():
        return item("tick_stall", "tick 会话拒开扫描", "skip", f"{log.name} 不存在")
    needles = ("InitPositionError", "IdleSpin:")
    hits: list[str] = []
    cur = ""
    try:
        with log.open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                s = ln.lstrip()
                if s.startswith("[20") and len(s) > 11 and s[5] == "-" and s[8] == "-":
                    cur = s[1:11]
                if cur != today:
                    continue
                if any(n in ln for n in needles):
                    hits.append(ln.strip()[:220])
                    if len(hits) >= 200:
                        break
    except OSError as exc:                      # noqa: BLE001 - 读不了要说出来
        return item("tick_stall", "tick 会话拒开扫描", "warn",
                    f"读 {log.name} 失败：{type(exc).__name__}: {exc}")
    if hits:
        return item("tick_stall", "tick 会话拒开扫描", "warn",
                    f"{today} 会话拒开/空转 ≥{len(hits)} 行：{hits[0]}",
                    on_red="查当日首个 InitPositionError/IdleSpin：通常是状态目录与账本"
                           "脱钩或分钟线没接上；确认后 ledger reset-state 并从账本重建")
    return item("tick_stall", "tick 会话拒开扫描", "green", f"{today} 无拒开/空转")


def check_action_funnel(account_db, today: str) -> dict:
    """交易动作终态（2026-11-23 事故派生，T102）。

    事故：计划 #28 的 `sell 000426 400` 与 `buy 600519 100` 被单轮处理上限
    截断，状态停在"拒绝：超出单轮处理上限（仅审计，未执行）"——账本没错，
    意图队列也没错，**单子从未进入执行链**，而 daily-check 全绿。

    判据：当日 `agent_decisions.result.decisions` 里每条 buy/sell 必须以
    **业务原因**结束（已入队/已成交/风控拒绝/池外/数量/价格/门禁等）；
    "超出单轮处理上限""未知动作"属于执行管道自身丢单 ⇒ **红**（不是钱类红：
    账本没动，但交易完整性已被破坏）。
    """
    with contextlib.closing(_ro(account_db)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                         " AND name='agent_decisions'").fetchone():
            return item("action_funnel", "交易动作终态", "skip",
                        "agent_decisions 表不存在")
        rows = [dict(r) for r in c.execute(
            "SELECT id, result_json, created_at FROM agent_decisions"
            " WHERE decision_date=? ORDER BY id", (today,))]

    if not rows:
        return item("action_funnel", "交易动作终态", "skip",
                    f"{today} 没有 AI 决策记录")

    total = 0
    bad: list[str] = []
    queued = 0
    rejected = 0
    for r in rows:
        try:
            result = json.loads(r.get("result_json") or "{}")
        except (TypeError, ValueError):
            bad.append(f"decision#{r['id']} 结果不是合法 JSON")
            continue
        for d in (result.get("decisions") or []):
            if not isinstance(d, dict):
                continue
            act = str(d.get("action") or "").lower()
            if act not in ("buy", "sell"):
                continue
            total += 1
            status = str(d.get("status") or "").strip()
            label = f"{d.get('symbol')} {act}"
            if not status:
                bad.append(f"{label} 无终态")
            elif "超出单轮处理上限" in status or "未知动作" in status:
                bad.append(f"{label}：{status[:60]}")
            elif status.startswith("已入意图队列") or status.startswith("已成交") \
                    or status.startswith("试运行通过"):
                queued += 1
            elif status.startswith("拒绝：") or status.startswith("风控拒绝：") \
                    or status.startswith("撮合拒绝："):
                rejected += 1
            else:
                bad.append(f"{label}：无法识别的终态 {status[:60]}")

    if bad:
        return item("action_funnel", "交易动作终态", "red",
                    f"{today} 有 {len(bad)} 笔买卖动作被执行管道丢单："
                    + "；".join(bad[:3]),
                    on_red="查 agent.log 当日 `AI decide done` 的逐条 status；"
                           "排序/额度只允许拒绝 hold，不允许丢买卖单")
    if total == 0:
        return item("action_funnel", "交易动作终态", "green",
                    f"{today} 无买卖动作")
    return item("action_funnel", "交易动作终态", "green",
                f"{today} 买卖动作 {total} 笔全部有业务终态"
                f"（入队/成交 {queued}、业务拒绝 {rejected}）")


def daily_check(*, data_db, account_db, symbols: list, today: str, cfg,
                state_dir: str = "var/rq_state/live",
                days: int = 60, heartbeat_path=None,
                now: Optional[datetime] = None, executor: str = "rqalpha",
                broker=None, work_expected: Optional[bool] = None) -> dict:
    """全套每日核对 = `m3 verify` 的 12 项 + 本模块 3 项，一个退出码。"""
    from paper_trading.utils import m3_verify

    base = (m3_verify.verify_all(broker=broker, data_db=data_db, symbols=symbols,
                                 today=today, cfg=cfg, days=days,
                                 heartbeat_path=heartbeat_path, now=now,
                                 executor=executor)
            if broker is not None else {"items": [], "today": today})
    items = list(base.get("items") or []) + [
        check_init_align(state_dir, account_db, today, work_expected=work_expected),
        check_carry_over(account_db, today, now=now, executor=executor),
        check_intent_chain(account_db, today, executor=executor),
        check_cond_loop(account_db, today, executor=executor),
        check_action_funnel(account_db, today),
        check_engine_crashes(account_db, today),
        check_tick_stall(account_db, today),
    ]
    reds = [i for i in items if i["status"] == "red"]
    items.sort(key=lambda i: m3_verify.STATUS_ORDER.get(i["status"], 9))
    return {"today": today, "items": items,
            "green": sum(1 for i in items if i["status"] == "green"),
            "warn": sum(1 for i in items if i["status"] == "warn"),
            "red": len(reds),
            "skipped": sum(1 for i in items if i["status"] == "skip"),
            "rollback_required": any(i["money"] for i in reds),
            "ok": not reds}
