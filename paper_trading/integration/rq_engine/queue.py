"""M3b 意图队列：旧线 evaluate 触发的意图 → rq 引擎分钟会话执行。

分工不变（AGENTS 规则纪律 1）：**判定在 L2**（rules.evaluate 免费快），
**执行/撮合在 L1**（rqalpha 分钟会话，T+1/涨跌停/费用全是框架代码）。
executor=rq 时 engine runner 把触发意图入队（不直接撮合），
`rq tick` 每 5 分钟消费：pending 意图 → run_session(1m, intraday_resume)
→ ledger 落账 → 意图标记。

幂等双保险（T52）：① intent_key（symbol[:6]_SIDE_vol）与账本当日 orders
对照跳过已执行；② 意图表 status 推进（queued → executed/rejected）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rq_intents (
    biz_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    volume INTEGER NOT NULL,
    rule_ref TEXT,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    result TEXT
)
"""

KEY_FMT = "{code}_{side}_{volume}"


def intent_key(symbol, side: str, volume: int) -> str:
    code = str(symbol).split(".")[0][:6].zfill(6)
    return KEY_FMT.format(code=code, side=str(side).upper(), volume=int(volume))


def _conn(db_path: str | Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path), timeout=15)
    c.execute("PRAGMA busy_timeout=15000")
    c.executescript(_SCHEMA)
    return c


def _same_key(biz_id: str, key: str) -> bool:
    """biz_id 是否是这个意图 key（裸 key 或 `key@时刻[#序号]`）。"""
    return biz_id == key or str(biz_id).startswith(key + "@")


def enqueue(db_path: str | Path, symbol, side: str, volume: int,
            rule_ref: str = "", detail: dict | None = None) -> str:
    """入队一个意图。

    biz_id 带时间戳唯一后缀（同 key 执行完再触发必须能入队——早先用 key
    当主键 + INSERT OR IGNORE 会把 executed 后的第二次触发静默吞掉）；
    未决去重只拦"同 key 已在队列里"的情况（第二道防线，第一道在引擎
    的 mark_order/每日同规则闸）。
    """
    key = intent_key(symbol, side, volume)
    ts = datetime.now().isoformat(timespec="seconds")
    with _conn(db_path) as c:
        # 精确前缀比对，不走 LIKE：key 里的 `_` 在 LIKE 里是单字符通配，会跨标的误命中
        rows = c.execute("SELECT biz_id, status FROM rq_intents").fetchall()
        if any(_same_key(r[0], key) and r[1] in ("pending", "queued") for r in rows):
            return key                    # 已有待决同款意图——幂等返回
        taken = {r[0] for r in rows}
        biz = f"{key}@{ts}"
        seq = 0
        while biz in taken:              # 同一秒内"执行完又触发"会撞主键 ⇒ 加序号，不许多吞一次
            seq += 1
            biz = f"{key}@{ts}#{seq}"
        c.execute(
            "INSERT INTO rq_intents"
            " (biz_id, symbol, side, volume, rule_ref, detail, status, created_at)"
            " VALUES (?,?,?,?,?,?,'queued',?)",
            (biz, str(symbol).split(".")[0], str(side).upper(), int(volume),
             rule_ref, json.dumps(detail or {}, ensure_ascii=False), ts))
    return biz


def pending(db_path: str | Path) -> list[dict]:
    """待决意图（带 `decided_at` = 入队时刻，会话侧按它决定在哪根 bar 提交）。"""
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT biz_id, symbol, side, volume, created_at FROM rq_intents"
            " WHERE status IN ('pending','queued') ORDER BY created_at").fetchall()
    return [{"biz_id": r[0], "symbol": r[1], "action": r[2].lower(),
             "volume": int(r[3]), "decided_at": r[4]} for r in rows]


def backlog(db_path: str | Path) -> dict:
    """队列体检（m3 verify 用）：状态分布 + 最老未决意图的年龄。"""
    with _conn(db_path) as c:
        try:
            dist = {r[0]: int(r[1]) for r in c.execute(
                "SELECT status, COUNT(*) FROM rq_intents GROUP BY status")}
            oldest = c.execute(
                "SELECT MIN(created_at) FROM rq_intents"
                " WHERE status IN ('pending','queued')").fetchone()[0]
        except sqlite3.OperationalError:            # 表还没建（旧线未切换）
            return {"exists": False}
    return {"exists": True, "by_status": dist, "oldest_pending": oldest}


def expire_stale(db_path: str | Path, day: str) -> list[str]:
    """把**早于今天**的未决意图标 expired 并返回其 biz_id。

    规则：意图是"某个交易日的某个分钟做出的决定"，隔日再执行就是用昨天的决定
    交易今天的价格 ⇒ 宁可过期让规则重新触发，也不跨日补执行（跨日补单是不可归因的）。
    """
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT biz_id FROM rq_intents WHERE status IN ('pending','queued')"
            " AND substr(created_at,1,10) < ?", (day,)).fetchall()
        ids = [r[0] for r in rows]
        for b in ids:
            c.execute("UPDATE rq_intents SET status='expired', result=? WHERE biz_id=?",
                      (json.dumps({"reason": "跨日未执行，过期（决策只在当日内有效）",
                                   "expired_on": day}, ensure_ascii=False), b))
    return ids


def _code6(symbol) -> str:
    return str(symbol).split(".")[0][:6].zfill(6)


def resolve_outcomes(intents: list[dict], posted: list[dict], orders: list[dict],
                     submits: list[dict], waiting: Optional[set] = None,
                     *, posting_ok: bool = True) -> dict:
    """会话结果 → 逐笔队列状态（M3 接管②，修 P0-3 的核心判定）。

    规则（**只做有证据的推进**）：
    1. `posted`（T47-G1 真正落进账本的那笔，带 symbol/side/volume/order_id）按
       (标的, 方向, 量) 逐笔认领，一条只能认领一次 ⇒ 买 100 的成交绝不会替
       同标的"卖 100"或"买 200"完成意图；
    2. 账本当日已有同款成交（会话回报 SKIPPED_EXECUTED）⇒ 标 executed 并注明幂等命中，
       留在队列里只会每跳空转；
    3. 提交过但没有落账成交 ⇒ `rejected` + 框架原因（含收盘未成交被 after_trading
       拒掉的那种），**不许静默消失**；
    4. 没提交（决策时点未到 / 等对齐 bar）⇒ 保持 queued，下一跳继续；
    5. 落账失败（`posting_ok=False`）⇒ 谁都不许多标，全部留在队列里等归因。
    """
    waiting = set(waiting or ())
    pool = [p for p in (posted or []) if p]
    by_biz: dict = {}
    for o in (orders or []):
        bid_o = str(o.get("biz_id") or "")
        if not bid_o:
            continue
        prev = by_biz.get(bid_o)
        # 同一 biz_id 一天内可能被多根 bar 记到：真状态优先，补记的 SKIPPED_*
        # 不许把 FILLED/REJECTED/NOT_CREATED 盖掉（否则"没成交"能被洗成"已成交"）
        if prev is None or str(prev.get("status", "")).startswith("SKIPPED"):
            by_biz[bid_o] = o
    submitted = {str(s.get("biz_id") or "") for s in (submits or []) if s.get("biz_id")}
    updates: list[dict] = []
    stats = {"executed": 0, "executed_idempotent": 0, "rejected": 0, "kept": 0}
    if not posting_ok:
        return {"updates": [], "stats": {"kept": len(intents)},
                "unclaimed": [], "needs_attention": [
                    {"biz_id": it.get("biz_id"), "why": "落账失败，全部保持未决"}
                    for it in intents][:5]}
    for it in intents:
        bid = str(it.get("biz_id") or "")
        side = str(it.get("side") or ("BUY" if str(it.get("action", "")).lower() == "buy"
                                       else "SELL")).upper()
        want = (_code6(it.get("symbol")), side, int(it.get("volume") or 0))
        hit = next((i for i, p in enumerate(pool) if (
            _code6(p.get("symbol")), str(p.get("side") or "BUY").upper(),
            int(p.get("volume") or 0)) == want), None)
        if hit is not None:
            p = pool.pop(hit)
            updates.append({"biz_id": bid, "status": "executed",
                            "result": {"order_id": p.get("order_id"),
                                       "price": p.get("price"),
                                       "fill_biz_id": p.get("biz_id")}})
            stats["executed"] += 1
            continue
        o = by_biz.get(bid) or {}
        status = str(o.get("status") or "")
        if status == "SKIPPED_EXECUTED":
            updates.append({"biz_id": bid, "status": "executed",
                            "result": {"reason": "幂等命中：当日同款已成交"}})
            stats["executed_idempotent"] += 1
            continue
        if status == "INSTRUMENT_UNAVAILABLE":
            # bundle 里没这只标的（北交所/未收录新股）：当场 rejected 并给出原因，
            # 否则它会在队列里一直"等"到次日被 expire_stale 悄悄清掉（丢单且不可归因）
            updates.append({"biz_id": bid, "status": "rejected",
                            "result": {"reason": str(o.get("message")
                                                     or "标的不在 bundle 里"),
                                       "order_status": status}})
            stats["rejected"] += 1
            continue
        if status == "SKIPPED_DUPLICATE":
            # 会话内去重挡下的第二条同款意图：账本里**没有**它的成交，
            # 标 executed 就是"意图数与成交笔数对不上"的那种假账
            updates.append({"biz_id": bid, "status": "rejected",
                            "result": {"reason": str(o.get("message")
                                                     or "同跳同款意图去重：只提交第一笔")}})
            stats["rejected"] += 1
            continue
        if bid in submitted:
            why = str(o.get("message") or o.get("status") or "本跳未成交")[:160]
            updates.append({"biz_id": bid, "status": "rejected",
                            "result": {"reason": why, "order_status": o.get("status")}})
            stats["rejected"] += 1
            continue
        stats["kept"] += 1
    return {"updates": updates, "stats": stats,
            "unclaimed": [{"symbol": p.get("symbol"), "side": p.get("side"),
                           "volume": p.get("volume")} for p in pool],
            "waiting": sorted(waiting)}


def apply_outcomes(db_path: str | Path, updates: list[dict]) -> dict:
    """按会话/落账结果精确推进队列状态（M3 接管②，修 P0-3 的"按标的批量标"）。

    只承认调用方给的**逐笔**结果：`{biz_id, status, result}`。
    没有结果的意图保持原状态（未决），绝不因为"同标的成交过"就顺带完成。
    """
    done = {"executed": 0, "rejected": 0, "kept": 0}
    with _conn(db_path) as c:
        for u in updates:
            st = str(u.get("status") or "")
            if st not in ("executed", "rejected", "failed"):
                continue
            c.execute("UPDATE rq_intents SET status=?, result=? WHERE biz_id=?",
                      (st, json.dumps(u.get("result") or {}, ensure_ascii=False),
                       u.get("biz_id")))
            done[st] = done.get(st, 0) + 1
    return done


def mark(db_path: str | Path, biz_id: str, status: str,
         result: dict | None = None) -> None:
    with _conn(db_path) as c:
        c.execute("UPDATE rq_intents SET status=?, result=? WHERE biz_id=?",
                  (status, json.dumps(result or {}, ensure_ascii=False), biz_id))


def executed_keys_from_ledger(db_path: str | Path, day: str) -> set[str]:
    """账本当日已成交意图 key（rq 侧落账的 orders/fills 反推）。

    归一到 intent_key 格式（symbol[:6]_SIDE_vol）——T52-R4 的防重复语义：
    只要今天这个 key 已有成交，队列里的同款意图一律跳过。
    """
    keys: set[str] = set()
    p = Path(db_path)
    if not p.exists():
        return keys
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        src = "fills" if "fills" in tables else "orders"
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({src})")}
        day_col = "timestamp" if "timestamp" in cols else (
            "trade_date" if "trade_date" in cols else "date")
        side_col = "direction" if "direction" in cols else "side"
        rows = c.execute(
            f"SELECT symbol, {side_col}, volume FROM {src}"
            f" WHERE {day_col} LIKE ?", (f"{day}%",)).fetchall()
        for sym, side, vol in rows:
            s = str(side)
            # fills.direction 是 int（paper_broker：1 买 / -1 卖）；orders.side 可能是文本
            norm = "BUY" if s in ("1", "buy", "BUY", "B") else "SELL"
            keys.add(intent_key(sym, norm, vol))
    finally:
        c.close()
    return keys
