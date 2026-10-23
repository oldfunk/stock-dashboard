"""盘中守护进程：每 interval 秒轮询实时价 → 规则判定 → 过风控 → 撮合。

与现有进程的关系：
- 复用 run_lock：与 cron/agent 手工补跑互斥，不会并发下单
- 复用 RiskManager + PaperBroker：与 LLM 交易完全相同的风控与成本口径
- 日内预算、每标的冷却、规则 once 语义全部落库（engine_state），重启不丢
- 任何异常都记流水并继续下一轮，绝不因单条规则崩盘
"""
from __future__ import annotations

import signal
import time
from datetime import datetime
from typing import Optional

from paper_trading.engine.config import load_engine_config, validate
from paper_trading.engine.rules import (INTRADAY_TYPES, PositionView, RuleContext,
                                        evaluate, size_for_value)
from paper_trading.engine.state import EngineState
from paper_trading.models import Order, OrderType, Signal, SignalType
from paper_trading.utils import get_logger

logger = get_logger(__name__)

_STOP = False

#: 分钟线补拉节流：同一标的两次补拉至少间隔这么久（东财限流较敏感）
PULL_GAP_SEC = 300


def _on_signal(signum, frame):    # noqa: ANN001, ARG001
    global _STOP
    _STOP = True
    logger.info("Engine stopping (signal)")


def bars_excluding_today(bars: list, now: datetime) -> list:
    """排除当日未收盘日线（收盘后 15:00 才把当日视为已收盘）。

    审计 P0：引擎盘中读到当日未完成 K 线会造出 T+0/未来函数信号。
    """
    today = now.date()
    after_close = (now.hour * 60 + now.minute) >= 15 * 60
    return [b for b in bars
            if b.timestamp.date() < today
            or (after_close and b.timestamp.date() == today)]


def price_map(bridge, quotes: dict, symbols: list[str]) -> dict[str, float]:
    """各标的最新价：实时优先，缺失回退最近已收盘日线（绝不把别家价格填给它）。

    审计 P0：原来风控把当前标的价格填给所有持仓 → 总仓位上限失真。
    """
    out: dict[str, float] = {}
    for s in symbols:
        q = quotes.get(s)
        if q and float(q.get("price") or 0.0) > 0:
            out[s] = float(q["price"])
            continue
        bars = bridge.data_db.get_bars(s, limit=1)
        if bars:
            out[s] = float(bars[-1].close)
    return out


def account_guard(bridge, prices: dict, day: str) -> dict:
    """账户级熔断：更新峰值 + 组合回撤 + 当日亏损。引擎/条件单/AI 共用。"""
    nav = bridge.broker.get_nav(prices)
    risk = bridge.risk
    risk.update_peak(nav.total_value)
    dd_halt, dd = risk.check_drawdown(nav.total_value)
    dl_halt, dl = risk.check_daily_loss(nav.total_value, day)
    why = []
    if dd_halt:
        why.append(f"组合回撤 {dd:.2%}")
    if dl_halt:
        why.append(f"当日亏损 {dl:.2%}")
    return {"nav": nav, "halted": bool(why), "drawdown": dd, "daily_loss": dl,
            "why": "、".join(why)}


class EngineRunner:
    """一轮 = 拉实时价 + 判规则 + 执行。无 LLM、无网络依赖（行情源失败即跳过）。"""

    def __init__(self, bridge, cfg: Optional[dict] = None,
                 state: Optional[EngineState] = None,
                 dry_run: bool = False):
        self.b = bridge
        self.cfg = cfg if cfg is not None else load_engine_config(
            getattr(bridge, "config_path", "config.yaml"))
        self.state = state or EngineState(bridge.account_db_path)
        self.dry_run = dry_run

    # ---------- 工具 ----------

    def _pool_symbols(self) -> list[str]:
        """engine.universe=auto 时，未点名标的的规则取本地股票池（有上限）。"""
        if str(self.cfg.get("universe") or "") != "auto":
            return []
        try:
            pool = self.b.data_db.get_pool_symbols()
        except Exception:      # noqa: BLE001 - 池读不到就退回"无标的"
            pool = []
        cap = int(self.cfg.get("universe_max") or 50)
        return [str(s) for s in pool[:max(1, cap)]]

    def _rule_syms_of(self, rule: dict) -> list[str]:
        syms = rule.get("symbols") or ([rule.get("symbol")] if rule.get("symbol")
                                       else [])
        if not syms:
            syms = self._pool_symbols()
        return [str(s) for s in syms]

    def _rule_symbols(self) -> list[str]:
        out: list[str] = []
        for r in self.cfg.get("rules") or []:
            out.extend(self._rule_syms_of(r))
        seen, res = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                res.append(s)
        return res

    def _position_view(self, sym: str) -> Optional[PositionView]:
        p = self.b.broker.get_position(sym)
        if not p or p.total_volume <= 0:
            return None
        return PositionView(symbol=sym, total_volume=p.total_volume,
                            available_volume=p.available_volume,
                            avg_cost=p.avg_cost,
                            entry_day=str(p.entry_day or ""),
                            peak_price=float(p.peak_price or 0.0))

    def _held_days(self, sym: str) -> int:
        """持有交易日数 = 建仓日之后**已收盘**的交易日数。

        与回测 `trading_days_held`（day_idx − entry_idx）同口径：引擎开盘时
        最新一根未收盘日线不计入，所以实盘在 T+1 日开盘才会比 T 日多一天。
        """
        pos = self.b.broker.get_position(sym)
        if not pos:
            return 0
        entry = str(pos.entry_day or "")
        if not entry:
            return 0
        hist = bars_excluding_today(self.b.data_db.get_bars(sym, limit=120),
                                    datetime.now())
        return len([b for b in hist
                    if b.timestamp.date().isoformat() > entry])

    def _refresh_peak(self, sym: str, quote: Optional[dict], now: datetime) -> None:
        """持仓峰值 = max(落库峰值, 成本价, 实时价/当日高点, 建仓以来日线高点)，落库。"""
        pos = self.b.broker.get_position(sym)
        if not pos or pos.total_volume <= 0:
            return
        entry = str(pos.entry_day or "")
        peak = max(float(pos.peak_price or 0.0), float(pos.avg_cost or 0.0))
        if quote:
            peak = max(peak, float(quote.get("price") or 0.0),
                       float(quote.get("high") or 0.0))
        hist = bars_excluding_today(self.b.data_db.get_bars(sym, limit=120), now)
        for b in hist:
            if not entry or b.timestamp.date().isoformat() >= entry:
                peak = max(peak, float(b.high or 0.0))
        if peak > 0:
            self.b.broker.update_peak_price(sym, peak)

    # ---------- 单轮 ----------

    def tick(self) -> dict:
        now = datetime.now()
        day = now.date().isoformat()
        out = {"ts": now.isoformat(), "day": day, "fired": [], "skipped": [],
               "quotes": 0}
        if not validate(self.cfg)[0]:
            out["error"] = f"规则配置非法：{validate(self.cfg)[1]}"
            return out
        syms = self._rule_symbols()
        if not syms:
            out["error"] = "无规则标的"
            return out

        from paper_trading.data import realtime as rt
        if not rt.is_trading_session():
            out["error"] = "非交易时段"
            return out
        # 持仓标的也要取价：风控按各自真实价格算总仓位（不再拿规则标的价填全场）
        held = [p.symbol for p in self.b.broker.get_all_positions()]
        quote_syms = sorted(set(syms) | set(held))
        quotes = rt.get_quotes(quote_syms, force=True)
        out["quotes"] = len(quotes)
        try:
            self.b.data_db.log_quote(now.isoformat(timespec="seconds"), quotes)
        except Exception as e:      # noqa: BLE001
            logger.debug(f"quote log failed: {e}")
        if not quotes:
            out["error"] = "行情源无数据"
            return out

        # 先维护持仓峰值（trailing_stop 的判定输入），再算账户级熔断
        for sym in held:
            self._refresh_peak(sym, quotes.get(sym), now)
        prices = price_map(self.b, quotes, quote_syms)
        guard = account_guard(self.b, prices, day)
        out["nav"] = round(guard["nav"].total_value, 2)
        if guard["halted"]:
            out["halted"] = {"why": guard["why"],
                             "drawdown": round(guard["drawdown"], 4),
                             "daily_loss": round(guard["daily_loss"], 4)}
            out["error"] = f"账户熔断：{guard['why']}"
            return out

        fired_today = self.state.fired_rules(day)
        budget = self.state.budget(day)
        cooldown = int(self.cfg.get("per_symbol_cooldown_sec") or 0)
        min_ticket = float(self.cfg.get("min_ticket_cny") or 0.0)
        max_orders = int(self.cfg.get("max_orders_per_day") or 0)
        max_turnover = float(self.cfg.get("max_turnover_per_day") or 0.0)

        for rule in self.cfg.get("rules") or []:
            rid = str(rule.get("id"))
            if rule.get("enabled") is False:      # 规则级开关（审计 P0）
                continue
            if rule.get("once", True) and rid in fired_today:
                continue
            act = rule.get("action") or {}
            for sym in self._rule_syms_of(rule):
                q = quotes.get(str(sym))
                if not q or not q.get("price"):
                    continue
                ctx = self._context(str(sym), q, now, day)
                try:
                    intent = evaluate(rule, ctx)
                except Exception as e:      # noqa: BLE001
                    out["skipped"].append({"rule": rid, "symbol": sym,
                                           "why": f"判定异常 {e}"})
                    continue
                if not intent:
                    continue
                res = self._execute(rule, intent, q, now, day, budget,
                                     cooldown, min_ticket, max_orders,
                                     max_turnover, prices)
                (out["fired"] if res.get("ok") else out["skipped"]).append(
                    {"rule": rid, "symbol": sym, "side": intent.side,
                     "reason": intent.reason, **res})
                if res.get("ok") and rule.get("once", True):
                    fired_today.add(rid)
                    self.state.mark_fired(rid, day)
                break
        out["budget"] = self.state.budget(day)
        return out

    def _context(self, sym: str, q: dict, now, day: str) -> RuleContext:
        # 排除当日未收盘日线：只用最近已收盘交易日的收盘序列（防 T+0/未来函数）
        bars = bars_excluding_today(self.b.data_db.get_bars(sym, limit=61), now)
        closes = [b.close for b in bars]
        prev_close = float(q.get("prev_close") or 0.0)
        price = float(q.get("price") or 0.0)
        chg = float(q.get("change_pct") or 0.0)
        return RuleContext(now=now.isoformat(), day=day, symbol=sym,
                           price=price, prev_close=prev_close,
                           day_high=float(q.get("high") or price),
                           day_low=float(q.get("low") or price),
                           change_pct=chg, closes=closes,
                           position=self._position_view(sym),
                           trading_days_held=self._held_days(sym),
                           minutes=self._minutes_for(sym, now, day))

    # ---------- 分钟线（仅当日、仅日内规则需要） ----------

    def _intraday_symbols(self) -> set[str]:
        out: set[str] = set()
        for r in self.cfg.get("rules") or []:
            if str(r.get("type") or "") not in INTRADAY_TYPES:
                continue
            out.update(self._rule_syms_of(r))
        return out

    def _minutes_for(self, sym: str, now, day: str) -> list[tuple]:
        """当日分钟线（按序，且**不含未来分钟**：ts > now 的一律丢弃）。

        库里没有或太旧时按需补拉一次（每 PULL_GAP 秒最多一次，防打爆行情源）。
        """
        if sym not in self._intraday_symbols():
            return []
        rows = self._today_minutes(sym, now, day)
        last = rows[-1][0] if rows else ""
        stale = True
        if last:
            try:
                stale = (now - datetime.fromisoformat(last)).total_seconds() > PULL_GAP_SEC
            except Exception:
                stale = True
        if stale and now.strftime("%H:%M") >= "09:35" and self.b.data_db is not None:
            try:
                from paper_trading.data.minute import fetch_minute

                fetched = fetch_minute(sym, period="5", days=30, now=now)
                if fetched:
                    self.b.data_db.upsert_minute_bars(sym, fetched)
                    rows = self._today_minutes(sym, now, day)
            except Exception as e:      # noqa: BLE001 - 分钟线拿不到就跳过日内规则
                logger.debug(f"minute refresh failed {sym}: {type(e).__name__}: {e}")
        return rows

    def _today_minutes(self, sym: str, now, day: str) -> list[tuple]:
        out: list[tuple] = []
        for r in self.b.data_db.get_minute_bars(sym, limit=600):
            ts = str(r.get("ts") or "")
            if not ts.startswith(day):
                continue
            try:                       # 未来分钟一律丢弃（回测/实盘同口径）
                if datetime.fromisoformat(ts) > now:
                    continue
            except ValueError:
                continue
            out.append((ts, float(r.get("open") or 0), float(r.get("high") or 0),
                        float(r.get("low") or 0), float(r.get("close") or 0),
                        float(r.get("volume") or 0), float(r.get("amount") or 0)))
        return out

    def _execute(self, rule: dict, intent, q: dict, now, day: str, budget: dict,
                 cooldown: int, min_ticket: float, max_orders: int,
                 max_turnover: float, prices: Optional[dict] = None) -> dict:
        rid = intent.rule_id
        sym = intent.symbol
        side = intent.side
        price = float(q.get("price") or 0.0)
        detail = {"reason": intent.reason, "ref_price": price}
        # 冷却
        last = self.state.last_order_ts(sym)
        if last and cooldown > 0:
            try:
                gap = (now - datetime.fromisoformat(last)).total_seconds()
                if gap < cooldown:
                    return {"ok": False, "why": f"冷却中（剩 {cooldown - gap:.0f}s）"}
            except Exception:
                pass
        # 日内预算
        if int(budget.get("orders", 0)) >= max_orders > 0:
            return {"ok": False, "why": "日内笔数上限"}
        # 手数
        act = rule.get("action") or {}
        pos = self.b.broker.get_position(sym)
        if side == "sell":
            if not pos or pos.available_volume <= 0:
                return {"ok": False, "why": "无可卖持仓（T+1 或空仓）"}
            volume = pos.total_volume if act.get("all") else (
                int(act.get("volume") or 0) or pos.total_volume)
            volume = min(volume, pos.available_volume)
            if volume % 100 != 0:
                volume = (volume // 100) * 100
        else:
            value = float(act.get("value") or 0) or 10000.0
            if value < min_ticket:
                return {"ok": False, "why": f"低于最小下单金额 {min_ticket:.0f}"}
            volume = size_for_value(price, value)
            if volume <= 0:
                return {"ok": False, "why": "金额不足一手"}
        if volume <= 0:
            return {"ok": False, "why": "手数为 0"}
        amount = price * volume
        if max_turnover > 0 and budget.get("turnover", 0) + amount > max_turnover:
            return {"ok": False, "why": "日内金额上限"}

        # 风控（与 LLM 交易同一把尺子）
        sig = Signal(symbol=sym, direction=(SignalType.BUY if side == "buy"
                                            else SignalType.SELL),
                     volume=volume, price=price, reason="engine:" + intent.reason)
        positions = {p.symbol: p for p in self.b.broker.get_all_positions()}
        prices = prices or price_map(
            self.b, {sym: q},
            sorted({p.symbol for p in positions.values()} | {sym}))
        nav = self.b.broker.get_nav(prices)
        ok, why = self.b.risk.check_signal(sig, price, self.b.broker.get_cash(),
                                          positions, nav.total_value, prices=prices)
        if not ok:
            self.state.log_trigger(now.isoformat(), day, rid, sym, side,
                                   "risk_reject", {**detail, "why": why})
            return {"ok": False, "why": f"风控拒绝：{why}"}
        if self.dry_run:
            self.state.log_trigger(now.isoformat(), day, rid, sym, side,
                                   "dry_run", {**detail, "volume": volume})
            return {"ok": True, "why": "试运行", "volume": volume}
        if not self.cfg.get("enabled"):
            # enabled=false 的语义是"不开盘中自动交易"——cron 单轮（--once）也必须守
            # （原来只在守护入口查这个开关，cron 走单轮时绕过了它，会照常下单）
            # 但仍然落一条“被拦下的意图”，供 dry-run 观察/复盘；每天同规则同标的只记一次。
            self.state.log_trigger_once(
                key=f"rule:{rid}:{sym}:{side}", ts=now.isoformat(), day=day,
                rule_id=rid, symbol=sym, side=side, status="blocked_disabled",
                detail={**detail, "volume": volume,
                        "why": "engine.enabled=false"})
            return {"ok": False, "why": "引擎未启用（engine.enabled=false），不下单"}
        order = Order(symbol=sym, direction=sig.direction.value, volume=volume,
                      order_type=OrderType.LIMIT, limit_price=price,
                      prev_close=float(q.get("prev_close") or 0.0),
                      ref_high=float(q.get("high") or price),
                      ref_low=float(q.get("low") or price))
        res = self.b.broker.submit_order(order)
        st = res.status.value
        self.state.log_trigger(now.isoformat(), day, rid, sym, side, st,
                               {**detail, "volume": volume,
                                "filled_price": res.filled_price})
        if st != "filled":
            return {"ok": False, "why": f"撮合拒绝：{st}"}
        self.b.broker.log_operation(
            f"engine:{side}", {"rule": rid, "symbol": sym, "volume": volume,
                               "price": price, "reason": intent.reason[:120]},
            True, {"filled_price": res.filled_price, "commission": res.commission,
                   "stamp_duty": res.stamp_duty,
                   "transfer_fee": res.transfer_fee}, None, None)
        self.state.mark_order(sym, now.isoformat())
        self.state.bump_budget(day, 1, amount)
        return {"ok": True, "volume": volume, "price": res.filled_price,
                "why": f"成交 @ {res.filled_price:.2f}"}


def run_loop(bridge, interval_sec: int = 60, dry_run: bool = False,
             max_ticks: int = 0) -> list[dict]:
    """守护循环（Ctrl-C 退出）：每轮同时跑「规则引擎」与「条件单」。

    max_ticks>0 时跑够次数返回（单测/CI 用）。
    """
    from paper_trading.engine.conditional import ConditionalOrders

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    cfg = load_engine_config(getattr(bridge, "config_path", "config.yaml"))
    runner = EngineRunner(bridge, cfg=cfg, dry_run=dry_run)
    cond = ConditionRunner(bridge, ConditionalOrders(bridge.account_db_path),
                           dry_run=dry_run,
                           enabled=bool(cfg.get("enabled")),
                           min_ticket_cny=float(cfg.get("min_ticket_cny") or 0),
                           max_per_day=int(cfg.get("max_orders_per_day") or 8),
                           state=runner.state)   # 与规则引擎共用日内订单预算
    out: list[dict] = []
    n = 0
    while not _STOP:
        res: dict = {"ts": datetime.now().isoformat()}
        for name, fn in (("rules", runner.tick), ("conds", cond.tick)):
            try:
                r = fn()
                if r.get("fired") or r.get("filled") or r.get("error"):
                    logger.info(f"engine/{name}: {r}")
                res[name] = r
            except Exception as e:      # noqa: BLE001 - 单模块异常不拖垮另一模块
                logger.error(f"engine/{name} crashed: {type(e).__name__}: {e}")
                res[name] = {"error": f"{type(e).__name__}: {e}"}
        out.append(res)
        n += 1
        if max_ticks and n >= max_ticks:
            break
        for _ in range(max(1, int(interval_sec))):
            if _STOP:
                break
            time.sleep(1)
    return out


# ---------- 条件单（AI 下条件，引擎盯盘触发） ----------

class ConditionRunner:
    """条件单执行器：每轮用实时价检查 armed 条件单，触发即过风控下单。"""

    def __init__(self, bridge, orders, dry_run: bool = False,
                 min_ticket_cny: float = 0.0, max_per_day: int = 8,
                 enabled: bool = True, state: Optional[EngineState] = None):
        self.b = bridge
        self.orders = orders
        self.dry_run = dry_run
        self.min_ticket = float(min_ticket_cny or 0.0)
        self.max_per_day = int(max_per_day or 8)
        # 与规则引擎同一个总开关：engine.enabled=false 时条件单也不自动成交
        self.enabled = bool(enabled)
        # 与规则引擎共用日内订单预算（同一账户同一天的总量不被叠加绕过）
        self.state = state

    def tick(self, quotes: Optional[dict] = None) -> dict:
        from paper_trading.data import realtime as rt
        from paper_trading.engine.conditional import triggered

        now = datetime.now()
        day = now.date().isoformat()
        expired = self.orders.expire_due(now)
        pend = self.orders.pending(now)
        out = {"ts": now.isoformat(), "expired": expired, "armed": len(pend),
               "filled": [], "rejected": [], "held": [], "n_quotes": 0}
        if not pend:
            return out
        # 生产 cron 传 quotes=None：休市/午休/节假日直接跳过，不拉过期报价。
        # 单测可显式注入 quotes，不受真实时钟影响。
        if quotes is None and not rt.is_trading_session(now):
            out["skipped"] = "not-trading-session"
            return out
        held_syms = [p.symbol for p in self.b.broker.get_all_positions()]
        syms = sorted({d["symbol"] for d in pend} | set(held_syms))
        q = quotes if quotes is not None else rt.get_quotes(syms, force=True)
        out["n_quotes"] = len(q)
        if not q:
            return out
        try:
            self.b.data_db.log_quote(now.isoformat(timespec="seconds"), q)
        except Exception:
            pass
        prices = price_map(self.b, q, syms)
        guard = account_guard(self.b, prices, day)
        if guard["halted"]:
            out["halted"] = {"why": guard["why"],
                             "drawdown": round(guard["drawdown"], 4),
                             "daily_loss": round(guard["daily_loss"], 4)}
            for d in pend:
                out["held"].append({"id": d["id"], "symbol": d["symbol"],
                                    "why": f"账户熔断：{guard['why']}，保持待触发"})
            return out
        # 日内预算与规则引擎共用（budget 由两个模块共同累加）
        budget = self.state.budget(day) if self.state is not None else {"orders": 0}
        for d in pend:
            if int(budget.get("orders", 0)) >= self.max_per_day > 0:
                out["rejected"].append({"id": d["id"],
                                        "why": "日内订单预算已用完（与规则引擎共用）"})
                continue
            quote = q.get(d["symbol"])
            if not quote or not quote.get("price"):
                continue
            price = float(quote["price"])
            chg = float(quote.get("change_pct") or 0.0)
            if not triggered(d, price, chg):
                continue
            # 离场优先：同标的当日已有卖出成交（规则止损/时间止损/条件卖出），
            # 买入条件作废，禁止“刚止损又当日回补”的来回打脸与双边手续费。
            if d["side"] == "buy" and self.b.broker.has_sell_fill_on(d["symbol"], day):
                why = "同标的当日已有卖出成交（离场优先），买入条件作废"
                out["rejected"].append({"id": d["id"], "symbol": d["symbol"],
                                        "why": why})
                self.orders.mark(d["id"], "rejected", price, {"why": why}, now)
                continue
            res = self._execute(d, quote, price, now, day, prices)
            rec = {"id": d["id"], "symbol": d["symbol"], "side": d["side"], **res}
            if res.get("ok"):
                out["filled"].append(rec)
                if self.state is not None and not self.dry_run:
                    budget = self.state.bump_budget(
                        day, 1, float(price) * int(res.get("volume") or 0))
            elif res.get("keep_armed"):
                # 引擎没开：条件本身没问题，只是现在不该成交 → 保持待触发
                out["held"].append(rec)
            else:
                out["rejected"].append(rec)
                self.orders.mark(d["id"], "rejected", price,
                                 {"why": res.get("why")}, now)
        return out

    def _execute(self, d: dict, quote: dict, price: float, now, day: str,
                 prices: Optional[dict] = None) -> dict:
        from paper_trading.engine.rules import size_for_value
        from paper_trading.models import Order, OrderType, Signal, SignalType

        sym, side = d["symbol"], d["side"]
        pos = self.b.broker.get_position(sym)
        if side == "sell":
            if not pos or pos.available_volume <= 0:
                return {"ok": False, "why": "无可卖持仓（T+1 或空仓）"}
            volume = int(d["volume"] or 0) or pos.available_volume
            volume = min(volume, pos.available_volume)
            volume = (volume // 100) * 100
        else:
            volume = int(d["volume"] or 0)
            if not volume:
                value = float(d["value"] or 0)
                if value < self.min_ticket:
                    return {"ok": False, "why": f"低于最小金额 {self.min_ticket:.0f}"}
                volume = size_for_value(price, value)
            if volume % 100 != 0:
                volume = (volume // 100) * 100
            # 显式 volume 也要过手续费地板：100 股低价股可能只有几百元，
            # 不拦就会绕过 min_ticket_cny（卖出不拦，清仓优先）。
            if self.min_ticket > 0 and volume > 0 and volume * price < self.min_ticket:
                return {"ok": False,
                        "why": f"买入金额 {volume * price:.0f} 低于最小金额 {self.min_ticket:.0f}"}
        if volume <= 0:
            return {"ok": False, "why": "手数为 0"}
        sig = Signal(symbol=sym, direction=(SignalType.BUY if side == "buy"
                                            else SignalType.SELL),
                     volume=volume, price=price, reason="cond:" + (d.get("note") or ""))
        positions = {p.symbol: p for p in self.b.broker.get_all_positions()}
        prices = prices or price_map(
            self.b, {sym: quote},
            sorted({p.symbol for p in positions.values()} | {sym}))
        nav = self.b.broker.get_nav(prices)
        ok, why = self.b.risk.check_signal(sig, price, self.b.broker.get_cash(),
                                          positions, nav.total_value, prices=prices)
        if not ok:
            return {"ok": False, "why": f"风控拒绝：{why}"}
        if self.dry_run:
            self.orders.mark(d["id"], "armed", price,
                             {"dry_run": True, "volume": volume}, now)
            return {"ok": True, "why": "试运行", "volume": volume}
        if not self.enabled:
            # 总开关关着就不成交（保持 armed，等人再开）；同时落 dry-run 意图流水。
            if self.state is not None:
                self.state.log_trigger_once(
                    key=f"cond:{d['id']}", ts=now.isoformat(), day=day,
                    rule_id=f"cond:{d['id']}", symbol=sym, side=side,
                    status="blocked_disabled",
                    detail={"volume": volume, "price": price,
                            "cond": f"{d['cond_type']}{d['cond_value']}",
                            "why": "engine.enabled=false"})
            return {"ok": False, "keep_armed": True,
                    "why": "引擎未启用（engine.enabled=false），不成交"}
        order = Order(symbol=sym, direction=sig.direction.value, volume=volume,
                      order_type=OrderType.LIMIT, limit_price=price,
                      prev_close=float(quote.get("prev_close") or 0.0),
                      ref_high=float(quote.get("high") or price),
                      ref_low=float(quote.get("low") or price))
        res = self.b.broker.submit_order(order)
        st = res.status.value
        if st != "filled":
            return {"ok": False, "why": f"撮合拒绝：{st}"}
        self.orders.mark(d["id"], "filled", res.filled_price,
                         {"volume": volume, "commission": res.commission,
                          "stamp_duty": res.stamp_duty,
                          "transfer_fee": res.transfer_fee}, now)
        self.b.broker.log_operation(
            f"cond:{side}", {"order_id": d["id"], "symbol": sym, "volume": volume,
                             "price": price, "cond": f"{d['cond_type']}"
                                                      f"{d['cond_value']}"},
            True, {"filled_price": res.filled_price,
                   "commission": res.commission,
                   "stamp_duty": res.stamp_duty,
                   "transfer_fee": res.transfer_fee}, None, None)
        return {"ok": True, "volume": volume, "price": res.filled_price,
                "why": f"条件触发，成交 @ {res.filled_price:.2f}"}