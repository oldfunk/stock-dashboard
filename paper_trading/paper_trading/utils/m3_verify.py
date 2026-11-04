"""M3 切换后验证（T46）：把"红了怎么办"写成代码，而不是写在便签上。

设计约束（都是这仓自己的教训）：
- **钱类项一律 fail-closed**：`money=True` 的检查只要不确定就判红，
  §5.6 的底线是"任何钱类项红 ⇒ 立即回退"，工具的职责是把"该不该回退"变成可判定；
- **不许静默跳过**：表不存在/数据不足 ⇒ `warn` 或 `skip` 并写清原因，
  绝不返回绿（AGENTS 纪律 4：大声；G-T13 家族反复撞过"0/0 看着像过了"）；
- **纯函数优先**：所有检查都是 `(conn/broker/db, today)` 的纯函数，
  网络与行情调用由调用方注入，NEC 无 rqalpha/无网也能跑全套；
- **双活检测**（`duplicate_fills`）是给切换日本身准备的：
  切换铁律是"不可双活一秒"，真双活了最直接的证据就是同一笔成交出现两行。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

from . import fees

#: 金额比对容差（元）。两侧都是 float 加总，1 分以下不算分歧（T38 同口径）。
MONEY_TOL = 0.01

#: 收盘 NAV 的"到点"时刻（T54 NEC 预跑照出来的盘中假红）：旧线由 16-18 点的
#: `agent tick` 收盘窗口写 NAV（crontab `*/5 16-18 * * 1-5`），给 5 分钟尾巴。
CLOSE_NAV_DUE = (18, 5)
#: 影子对账留痕的"到点"时刻：runbook §3 的 15:40 影子 cron + 5 分钟尾巴。
#: 未到点时"今天没有 parity:diff"是**正常**，文案不许指控"cron 没跑"。
PARITY_DUE = (15, 45)
#: 心跳文件的候选路径：先按 cwd（cron 与仓库根等价的情形），再按仓库根
#: （操作者从 `~` 或 scratch 目录跑 `m3 verify` 的情形——T54 实测就是这种，
#: 读不到文件被当成"应跳未跳"报了假红）。
HEARTBEAT_CANDIDATES = ("var/heartbeat.json",)

STATUS_ORDER = {"red": 0, "warn": 1, "skip": 2, "green": 3}


def item(cid: str, name: str, status: str, detail: str = "",
         *, money: bool = False, on_red: str = "归因") -> dict:
    return {"id": cid, "name": name, "status": status, "detail": detail,
            "money": money, "on_red": on_red}


def _day(ts: str) -> str:
    return str(ts or "")[:10]


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (name,)).fetchone())


# ---------------- 钱类检查 ----------------
def check_fees(conn, cfg, today: str) -> dict:
    """逐笔重算成交费用：佣金 `max(额×率, 地板)`、印花税只在卖出、过户费双向。

    这是"引擎接管后钱算得对不对"最直接的一刀：不依赖任何引擎内部记账，
    只拿 fills 表里的成交价量与现网费率重算一遍（T38 已证两侧应逐分相同）。
    """
    if not _has_table(conn, "fills"):
        return item("fees", "成交费用三件套重算", "skip", "fills 表不存在",
                    money=True, on_red="回退")
    fills = _rows(conn, "SELECT * FROM fills WHERE substr(timestamp,1,10)=?", (today,))
    if not fills:
        return item("fees", "成交费用三件套重算", "warn", "当天没有成交可验（0 笔）",
                    money=True, on_red="回退")
    bad = []
    for f in fills:
        amount = abs(float(f["price"]) * int(f["volume"]))
        # 公式从 `utils/fees.py` 来（T51 口径唯一）：检查用的尺子必须与 broker
        # 记账用的尺子是同一个函数，否则"红"可能是公式漂移而不是钱错
        if int(f["direction"]) == -1:
            want_commission, want_stamp, want_transfer = fees.sell_cost(cfg, amount)
        else:
            want_commission, want_transfer = fees.buy_cost(cfg, amount)
            want_stamp = 0.0
        deltas = {"commission": float(f["commission"] or 0) - want_commission,
                  "stamp_duty": float(f["stamp_duty"] or 0) - want_stamp,
                  "transfer_fee": float(f["transfer_fee"] or 0) - want_transfer}
        over = {k: round(v, 4) for k, v in deltas.items() if abs(v) > MONEY_TOL}
        if over:
            bad.append({"fill_id": f.get("fill_id"), "symbol": f["symbol"],
                        "amount": round(amount, 2), "delta": over})
    if bad:
        return item("fees", "成交费用三件套重算", "red",
                    json.dumps(bad[:3], ensure_ascii=False), money=True, on_red="回退")
    return item("fees", "成交费用三件套重算", "green",
                f"{len(fills)} 笔逐分相符（容差 {MONEY_TOL} 元）", money=True, on_red="回退")


def check_t_plus_one(conn, today: str) -> dict:
    """当天买入的必须不可卖（`available_volume=0`）；昨日买入的今早已放开。"""
    if not _has_table(conn, "positions"):
        return item("t_plus_one", "T+1 冻结正确", "skip", "positions 表不存在",
                    money=True, on_red="回退")
    pos = _rows(conn, "SELECT * FROM positions")
    viol = []
    for p in pos:
        entry = _day(p.get("entry_day") or "")
        total, avail = int(p["total_volume"] or 0), int(p["available_volume"] or 0)
        if total <= 0:
            continue
        if entry == today and avail != 0:
            viol.append({"symbol": p["symbol"], "entry_day": entry,
                         "total": total, "available": avail, "why": "当日买入可卖"})
        if entry and entry < today and avail > total:
            viol.append({"symbol": p["symbol"], "entry_day": entry,
                         "total": total, "available": avail, "why": "可卖大于持仓"})
    if viol:
        return item("t_plus_one", "T+1 冻结正确", "red",
                    json.dumps(viol[:3], ensure_ascii=False), money=True, on_red="回退")
    n_today = sum(1 for p in pos if _day(p.get("entry_day") or "") == today)
    return item("t_plus_one", "T+1 冻结正确", "green",
                f"当日建仓 {n_today} 只均可卖量=0（entry_day 缺失时按旧线不判）",
                money=True, on_red="回退")


def check_nav(conn, today: str, now: Optional[datetime] = None) -> dict:
    """收盘 NAV：当天必须恰 1 行，且 `total_value = cash + market_value` 自洽。

    T54（NEC 预跑照出来的）：**盘中查今天**必然 0 行——旧线的收盘 NAV 由
    16-18 点的 `agent tick` 收盘窗口写（crontab `*/5 16-18 * * 1-5`）。
    切换日第一小时清单正好在那个时段跑，把"还没到点"报成**钱类红**
    ⇒ 等于指挥操作者"立即回退"，而线根本没坏。⇒ 未到点判 warn 并写明到点时间；
    过了点仍无行，照旧钱类红（真断供是必须响的）。
    """
    if not _has_table(conn, "nav_history"):
        return item("nav", "NAV 收盘记录", "skip", "nav_history 表不存在",
                    money=True, on_red="回退")
    rows = _rows(conn, "SELECT * FROM nav_history ORDER BY timestamp")
    if not rows:
        return item("nav", "NAV 收盘记录", "red", "一条 NAV 都没有", money=True,
                    on_red="回退")
    todays = [r for r in rows if _day(r["timestamp"]) == today]
    if len(todays) == 0:
        tn = now or datetime.now()
        not_due = (today == tn.date().isoformat()
                   and (tn.hour, tn.minute) < CLOSE_NAV_DUE)
        if not_due:
            return item("nav", "NAV 收盘记录", "warn",
                        f"{today} 尚无 NAV 行——收盘窗口未过（{CLOSE_NAV_DUE[0]:02d}:"
                        f"{CLOSE_NAV_DUE[1]:02d} 之后才该有），当前 {tn:%H:%M}",
                        money=True, on_red="回退")
        return item("nav", "NAV 收盘记录", "red", f"{today} 没有 NAV 行（面板会断供）",
                    money=True, on_red="回退")
    if len(todays) > 1:
        return item("nav", "NAV 收盘记录", "red",
                    f"{today} 有 {len(todays)} 行（必须幂等 1 行）", money=True, on_red="回退")
    bad = [r for r in todays
           if abs(float(r["total_value"]) - (float(r["cash"]) + float(r["market_value"])))
           > MONEY_TOL]
    if bad:
        return item("nav", "NAV 收盘记录", "red",
                    f"total≠cash+市值：{json.dumps(bad[0], ensure_ascii=False)[:160]}",
                    money=True, on_red="回退")
    days = {_day(r["timestamp"]) for r in rows}
    return item("nav", "NAV 收盘记录", "green",
                f"{today} 恰 1 行且自洽；库里共 {len(days)} 个净值日", money=True,
                on_red="回退")


def check_duplicate_fills(conn, today: str) -> dict:
    """双活检测：同一 (symbol, direction, price, volume, 分钟) 出现两行 ⇒ 两条线都在下单。"""
    if not _has_table(conn, "fills"):
        return item("duplicate_fills", "旧新双线未双活", "skip", "fills 表不存在",
                    money=True, on_red="回退")
    rows = _rows(conn, """SELECT symbol, direction, price, volume,
                                 substr(timestamp,1,16) m, COUNT(*) n
                          FROM fills WHERE substr(timestamp,1,10)=?
                          GROUP BY symbol, direction, price, volume, m HAVING n>1""",
                 (today,))
    if rows:
        return item("duplicate_fills", "旧新双线未双活", "red",
                    "疑似双活（同一笔成交出现多行）：" + json.dumps(rows[:3], ensure_ascii=False),
                    money=True, on_red="回退")
    return item("duplicate_fills", "旧新双线未双活", "green", "无重复成交", money=True,
                on_red="回退")


def check_raw_gate(db, symbols, today, days: int = 60) -> dict:
    """引擎吃的原始价必须：有料、是 raw、新鲜、且逐日台账无未翻转（T41 的闸）。"""
    from paper_trading.data import raw_feed

    if not symbols:
        return item("raw_gate", "行情口径与新鲜度闸", "skip", "股票池为空")
    fresh = raw_feed.raw_freshness(db, symbols, today=date.fromisoformat(today))
    never = sorted(s for s, v in fresh.items() if v.get("reason") == "no_raw_data")
    stale = sorted(s for s, v in fresh.items() if v.get("stale"))
    lagged = [s for s in stale if s not in never]
    lags = sorted({fresh[s].get("lag") for s in lagged})
    flip = raw_feed.flip_watch_with_mother(db, symbols,
                                          today=date.fromisoformat(today), days=days)
    if stale or flip["violations"]:
        # 数量在前、样例在后：11-03 现网把这条读成"5 只未翻转"，真实情况是
        # **全池 38 只都缺 11-02 那根**——截断的名单直接把归因带偏（T60）。
        bits = []
        if lagged:
            bits.append(f"落后 {len(lagged)}/{len(symbols)} 只"
                        f"（落后 {lags[0]}~{lags[-1]} 个交易日，示例 {lagged[:3]}）")
        if never:
            bits.append(f"从未采集 {len(never)} 只"
                        f"（入池后没跑过全窗采集？示例 {never[:3]}）")
        if flip["violations"]:
            bits.append(f"台账未翻转 {len(flip['violations'])} 只"
                        f"（unflipped_days={flip['unflipped_days']}，"
                        f"示例 {flip['violations'][:3]}）")
        return item("raw_gate", "行情口径与新鲜度闸", "red",
                    "；".join(bits) + "——引擎会对这些标的 fail-closed 拒单",
                    on_red="回退（价都不对，账不可能对）")
    if not flip["audit_complete"] or flip["waiting_days"]:
        return item("raw_gate", "行情口径与新鲜度闸", "warn",
                    f"waiting_days={flip['waiting_days']} "
                    f"incomplete_days={flip['incomplete_days']} "
                    f"complete={flip['audit_complete']}", on_red="归因")
    return item("raw_gate", "行情口径与新鲜度闸", "green",
                f"{len(symbols)} 只全部新鲜且逐日台账完整", on_red="回退")


def check_minute_feed(data_db, symbols, today: str, now: Optional[datetime] = None,
                      executor: str = "rqalpha", max_lag_min: int = 20) -> dict:
    """分钟会话的命脉：`minute_bars` 必须有**当天**、且日内不陈旧的数据。

    M3 接管③：新线用 1 分钟会话撮合后，分钟线就是它的行情真相源。
    回填只解决历史；**日内不采集 = 新线没有 bar 可撮合**，
    表现是"意图在队列里静静过期"而不是报错——正是纪律 4 说的静默故障。
    """
    from pathlib import Path as _P

    tn = now or datetime.now()
    if executor != "rqalpha":
        return item("minute_feed", "分钟线供给（新线命脉）", "skip",
                    "executor 非 rqalpha，本项不适用")
    db_path = getattr(data_db, "db_path", None) or data_db
    try:
        conn = sqlite3.connect(f"file:{_P(str(db_path))}?mode=ro", uri=True)
    except Exception as exc:                        # noqa: BLE001
        return item("minute_feed", "分钟线供给（新线命脉）", "skip", f"库打不开：{exc}")
    try:
        try:
            latest = conn.execute("SELECT MAX(ts) FROM minute_bars").fetchone()[0]
        except sqlite3.OperationalError:
            return item("minute_feed", "分钟线供给（新线命脉）", "red",
                        "minute_bars 表不存在（新线无数据可撮合）", on_red="归因/采集")
        n_today = conn.execute("SELECT COUNT(*) FROM minute_bars"
                               " WHERE substr(ts,1,10)=?", (today,)).fetchone()[0]
        syms_today = conn.execute("SELECT COUNT(DISTINCT symbol) FROM minute_bars"
                                  " WHERE substr(ts,1,10)=?", (today,)).fetchone()[0]
    finally:
        conn.close()
    detail = f"今天 {n_today} 行 / {syms_today} 只；最新 {latest}"
    mins = tn.hour * 60 + tn.minute
    in_session = 9 * 60 + 5 <= mins <= 15 * 60 + 5
    if n_today == 0:
        return item("minute_feed", "分钟线供给（新线命脉）",
                    "red" if in_session else "warn",
                    f"{detail}——**没有当天分钟线**：日内采集未跑"
                    f"（{'盘中' if in_session else '非盘中'}）", on_red="归因/采集")
    if latest and in_session:
        from paper_trading.data.minute import latest_expected_ts

        # 陈旧度 = "此刻该有的一根"与"库里最新一根"差多少分钟——不能用墙钟：
        # 11:30 收午盘后到 13:00 之间没有新 bar 是**正确**的行为。
        want = latest_expected_ts(today, "5", tn)
        try:
            lag = 0.0 if not want else (
                (datetime.fromisoformat(want)
                 - datetime.fromisoformat(str(latest)[:16])).total_seconds() / 60.0)
        except ValueError:
            lag = (tn - datetime.fromisoformat(str(latest))).total_seconds() / 60.0
        if lag > max_lag_min:
            return item("minute_feed", "分钟线供给（新线命脉）", "red",
                        f"{detail}；比此刻该有的一根滞后 {lag:.0f} 分钟（阈值 {max_lag_min}）",
                        on_red="归因/采集")
    return item("minute_feed", "分钟线供给（新线命脉）", "green", detail,
                on_red="归因/采集")


def check_intent_queue(conn, today: str, now: Optional[datetime] = None,
                       executor: str = "rqalpha", backlog_min: int = 20) -> dict:
    """意图队列：executed 必须有落账流水、未决不许积压、跨日不许补执行。

    P0-3 的形状就是"标了 executed、账本里没有这笔"；这里把它变成每次验证必查项。
    """
    if not _has_table(conn, "rq_intents"):
        if executor != "rqalpha":
            return item("intent_queue", "意图队列与账本相符", "skip", "未切换（队列表未建）")
        return item("intent_queue", "意图队列与账本相符", "red",
                    "executor=rqalpha 但没有 rq_intents 表（引擎没在入队？）",
                    on_red="归因（配置/部署）")
    rows = _rows(conn, "SELECT biz_id, status, created_at, result FROM rq_intents"
                       " WHERE substr(created_at,1,10)>=? ORDER BY created_at", (today,))
    by: dict = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    phantom = [r["biz_id"] for r in rows
               if r["status"] == "executed" and "order_id" not in str(r.get("result") or "")]
    tn = now or datetime.now()
    stale_pending = []
    for r in rows:
        if r["status"] not in ("pending", "queued"):
            continue
        try:
            age_min = (tn - datetime.fromisoformat(str(r["created_at"]))
                       ).total_seconds() / 60.0
        except ValueError:
            age_min = 0.0
        if age_min > backlog_min:
            stale_pending.append({"biz_id": r["biz_id"], "age_min": round(age_min)})
    cross_day = [r["biz_id"] for r in _rows(
        conn, "SELECT biz_id FROM rq_intents WHERE status IN ('pending','queued')"
              " AND created_at < ?", (today + "T00:00:00",))]
    summary = f"今日 {len(rows)} 条 {by or {}}"
    if phantom:
        return item("intent_queue", "意图队列与账本相符", "red",
                    f"标 executed 却无落账 order_id 的意图 {len(phantom)} 条："
                    f"{phantom[:5]}（{summary}）", money=True,
                    on_red="回退/归因：意图被吞或落账失败")
    if stale_pending:
        return item("intent_queue", "意图队列与账本相符", "red",
                    f"未决意图积压 >{backlog_min} 分钟：{stale_pending[:3]}（{summary}）",
                    on_red="归因（rq tick 未跑/venv/采集）")
    if cross_day:
        return item("intent_queue", "意图队列与账本相符", "warn",
                    f"跨日未决 {len(cross_day)} 条（tick 未跑则不会自动过期）："
                    f"{cross_day[:5]}", on_red="归因")
    return item("intent_queue", "意图队列与账本相符", "green",
                summary if rows else "今日无意图（队列空）", on_red="回退/归因")


# ---------------- 审计与链路检查 ----------------
def check_parity_trace(conn, today: str, now: Optional[datetime] = None) -> dict:
    """影子对账留痕：当天要有 `parity:diff`，且三个 delta 全零或已归因。"""
    if not _has_table(conn, "op_log"):
        return item("parity_trace", "双轨影子留痕连续", "skip", "op_log 表不存在")
    rows = _rows(conn, "SELECT * FROM op_log WHERE action='parity:diff' "
                       "AND substr(timestamp,1,10)=? ORDER BY id", (today,))
    if not rows:
        tn = now or datetime.now()
        if today == tn.date().isoformat() and (tn.hour, tn.minute) < PARITY_DUE:
            return item("parity_trace", "双轨影子留痕连续", "warn",
                        f"{today} 尚无 parity:diff——影子时点未到"
                        f"（{PARITY_DUE[0]:02d}:{PARITY_DUE[1]:02d} 之后才该有），"
                        f"当前 {tn:%H:%M}")
        return item("parity_trace", "双轨影子留痕连续", "warn",
                    f"{today} 没有 parity:diff（影子 cron 没跑，或用了 --no-op-log）")
    unattr = []
    for r in rows:
        try:
            res = json.loads(r.get("result") or "{}")
        except json.JSONDecodeError:
            unattr.append({"id": r["id"], "why": "result 不是 JSON"})
            continue
        if int(r["ok"] or 0) != 1:
            unattr.append({"id": r["id"], "why": "ok=0"})
        if any(float(res.get(k) or 0) != 0.0 for k in ("cash_delta", "fee_delta", "fill_delta")):
            unattr.append({"id": r["id"], "why": "delta 非零"})
    if unattr:
        return item("parity_trace", "双轨影子留痕连续", "red",
                    json.dumps(unattr[:3], ensure_ascii=False), on_red="归因")
    return item("parity_trace", "双轨影子留痕连续", "green",
                f"{len(rows)} 行，delta 全零")


def check_audit_trail(conn, today: str) -> dict:
    """有成交就必须有下单动作的流水（成交 ⇔ 审计，缺一边就是无痕）。"""
    if not (_has_table(conn, "fills") and _has_table(conn, "op_log")):
        return item("audit_trail", "成交⇔审计流水齐备", "skip", "fills/op_log 缺表")
    n_fill = _rows(conn, "SELECT COUNT(*) c FROM fills WHERE substr(timestamp,1,10)=?",
                   (today,))
    acts = _rows(conn, """SELECT action, COUNT(*) n FROM op_log
                          WHERE substr(timestamp,1,10)=?
                            AND (action LIKE '%:buy' OR action LIKE '%:sell'
                                 OR action IN ('agent:tick','ai:decide','m3:switch','parity:diff'))
                          GROUP BY action""", (today,))
    fills = int(n_fill[0]["c"]) if n_fill else 0
    if fills and not acts:
        return item("audit_trail", "成交⇔审计流水齐备", "red",
                    f"{fills} 笔成交但当天没有任何执行/切换类流水")
    return item("audit_trail", "成交⇔审计流水齐备", "green",
                f"成交 {fills} 笔；流水 {json.dumps([{a['action']: a['n']} for a in acts], ensure_ascii=False)[:180]}")


def check_plan_chain(conn, today: str) -> dict:
    """AI 计划链：当天计划存在且状态可解释（pending 过夜要归因）。"""
    if not _has_table(conn, "agent_plans"):
        return item("plan_chain", "AI 计划执行链", "skip", "agent_plans 表不存在")
    rows = _rows(conn, "SELECT plan_date, status, COUNT(*) n FROM agent_plans "
                       "GROUP BY plan_date, status ORDER BY plan_date DESC LIMIT 10")
    todays = [r for r in rows if r["plan_date"] == today]
    if not todays:
        return item("plan_chain", "AI 计划执行链", "warn",
                    f"{today} 没有计划行（收盘做计划的 cron 可能没跑）")
    return item("plan_chain", "AI 计划执行链", "green",
                json.dumps(todays, ensure_ascii=False)[:200])


def check_conditional_chain(conn, today: str) -> dict:
    """条件单链：标了 triggered/executed 的必须有同日 fill，否则是"假触发"。"""
    if not _has_table(conn, "conditional_orders"):
        return item("cond_chain", "条件单触发链", "skip", "conditional_orders 表不存在")
    t = _rows(conn, """SELECT status, COUNT(*) n FROM conditional_orders
                       GROUP BY status""")
    trig = _rows(conn, """SELECT c.id, c.symbol FROM conditional_orders c
                          WHERE c.status IN ('triggered','executed','done')
                            AND NOT EXISTS (SELECT 1 FROM fills f
                                            WHERE f.symbol=c.symbol
                                              AND substr(f.timestamp,1,10)=?)""", (today,))
    if trig:
        return item("cond_chain", "条件单触发链", "warn",
                    f"{len(trig)} 条已触发但当天无该标的成交（多为撤单/涨停挡下，需归因）："
                    + json.dumps(trig[:3], ensure_ascii=False))
    return item("cond_chain", "条件单触发链", "green",
                json.dumps([{"status": r["status"], "n": r["n"]} for r in t])[:200])


def resolve_heartbeat_path(path=None, *, cwd=None, root=None) -> tuple:
    """心跳文件定位（T54：NEC 实测的假红就在这）。

    cron 的 cwd 是仓库根 ⇒ 相对路径能用；操作者从 `~` 或 scratch 目录跑
    `m3 verify` 时同一个相对路径读到的是"不存在的文件"，旧实现把它当成
    "应跳未跳"报**红**（`heartbeat.load()` 缺文件回退空 dict，ages 全 None）。
    ⇒ 候选顺序：显式 `--heartbeat` ＞ cwd/ ＞ 仓库根/；都没有就返回 None，
      由调用方判 `skip`（"没证据"不等于"线挂了"）。
    """
    from pathlib import Path

    if path:
        p = Path(path)
        return (p if p.exists() else None), [str(p)]
    _cwd = Path(cwd) if cwd else Path.cwd()
    _root = Path(root) if root else Path(__file__).resolve().parents[2]
    cands = [_cwd / HEARTBEAT_CANDIDATES[0], _root / HEARTBEAT_CANDIDATES[0]]
    out = [str(c) for c in cands]
    for c in cands:
        if c.exists():
            return c, out
    return None, out


def check_heartbeat(path=None, now: Optional[datetime] = None,
                    thresholds: Optional[dict] = None, *,
                    cwd=None, root=None, executor: str = "legacy") -> dict:
    """引擎/代理心跳：在"本该跳"的时段里超时 ⇒ 线根本没跑（面板与 cron 的唯一活着证据）。

    非应跳时段（周末/夜间）不判停滞，但会把"为什么没判"写出来——
    否则第一个早上所有人看到的都是假绿。
    """
    from paper_trading.utils import heartbeat as hb

    resolved, tried = resolve_heartbeat_path(path, cwd=cwd, root=root)
    if resolved is None:
        return item("heartbeat", "引擎/代理心跳", "skip",
                    f"找不到心跳文件（试过：{'; '.join(tried)}）——"
                    "请用 --heartbeat 指定，或在仓库根运行")
    th = dict(thresholds or hb.DEFAULT_THRESHOLDS)
    if executor == "rqalpha":
        th.setdefault("rq_tick", hb.RQ_TICK_THRESHOLD)
        # 采集器"装过才追问"：没装 intraday cron 的部署不该为它假红；装过又停了必须响
        if hb.age("minute", path=resolved, now=now) is not None:
            th.setdefault("minute", hb.MINUTE_THRESHOLD)
    try:
        ages = {n: hb.age(n, path=resolved, now=now) for n in th}
        dues = {n: hb.due(n, now=now) for n in th}
    except Exception as exc:                      # noqa: BLE001 - 读不到就明说
        return item("heartbeat", "引擎/代理心跳", "skip",
                    f"心跳读取失败：{exc}（文件 {resolved}）")
    late = [n for n in th
            if dues.get(n) and (ages.get(n) is None or ages[n] > th[n])]
    parts = []
    for n in th:
        a = ages.get(n)
        shown = "无记录" if a is None else f"{a:.0f}s"
        parts.append("%s=%s%s" % (n, shown, "（应跳）" if dues.get(n) else "（不在时段）"))
    detail = " ".join(parts) + f"｜文件 {resolved}"
    if late:
        return item("heartbeat", "引擎/代理心跳", "red",
                    f"应跳未跳：{late}；阈值 {th}", on_red="归因（cron/锁/进程）")
    if all(not dues.get(n) for n in th):
        return item("heartbeat", "引擎/代理心跳", "warn",
                    f"当前不在应跳时段，未判停滞：{detail}", on_red="归因")
    return item("heartbeat", "引擎/代理心跳", "green", detail, on_red="归因")


# ---------------- 汇总 ----------------
def verify_all(*, broker, data_db, symbols: list, today: str,
               cfg, days: int = 60, heartbeat_path=None,
               now: Optional[datetime] = None, executor: str = "legacy") -> dict:
    """跑全套并给结论；钱类红 ⇒ `rollback_required=True`（§5.6 底线）。"""
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    try:
        items = [
            check_fees(conn, cfg, today),
            check_t_plus_one(conn, today),
            check_nav(conn, today, now=now),
            check_duplicate_fills(conn, today),
            check_raw_gate(data_db, symbols, today, days=days),
            check_minute_feed(data_db, symbols, today, now=now, executor=executor),
            check_intent_queue(conn, today, now=now, executor=executor),
            check_parity_trace(conn, today, now=now),
            check_audit_trail(conn, today),
            check_plan_chain(conn, today),
            check_conditional_chain(conn, today),
            check_heartbeat(path=heartbeat_path, now=now, executor=executor),
        ]
    finally:
        conn.close()
    reds = [i for i in items if i["status"] == "red"]
    money_reds = [i for i in reds if i["money"]]
    items.sort(key=lambda i: STATUS_ORDER.get(i["status"], 9))
    return {"today": today, "items": items,
            "green": sum(1 for i in items if i["status"] == "green"),
            "warn": sum(1 for i in items if i["status"] == "warn"),
            "red": len(reds), "skipped": sum(1 for i in items if i["status"] == "skip"),
            "rollback_required": bool(money_reds),
            "money_reds": [i["id"] for i in money_reds],
            "on_red": {i["id"]: i["on_red"] for i in reds}}


def format_text(res: dict) -> str:
    mark = {"green": "✅", "red": "❌", "warn": "⚠️ ", "skip": "⚪"}
    # 池来源必须出现在人读输出里：T54 在 NEC 实测——操作者从 `~` 跑时 cwd 没有
    # config.yaml，标的池解出来是空的，raw_gate 于是报"股票池为空"；
    # 看不见来源就会把它误读成"没什么要查的"。
    pool = f"｜池 {res['pool_source']}" if res.get("pool_source") else ""
    lines = [f"M3 验证（{res['today']}{pool}）  绿 {res['green']} / 警 {res['warn']} / "
             f"红 {res['red']} / 跳 {res['skipped']}"]
    for i in res["items"]:
        tag = "钱" if i["money"] else " "
        lines.append(f"{mark.get(i['status'], '?')} [{tag}] {i['name']}：{i['detail']}"
                     + (f"  → 红了：{i['on_red']}" if i["status"] == "red" else ""))
    if res["rollback_required"]:
        lines.append("❗钱类项红 ⇒ 按 §5.6 底线**立即回退**（步骤见 docs/migration-m3.md §5.1）")
    return "\n".join(lines)
