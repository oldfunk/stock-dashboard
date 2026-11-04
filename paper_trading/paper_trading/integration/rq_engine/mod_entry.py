"""rqalpha mod 入口（模块名必须是 `rqalpha_mod_<mod_name>`，见 `mod/__init__.py:54`）。

`mod_name = "ptpaper"` → 本文件；`runner.py` 把本目录加入 `sys.path` 后用
`config.mod.ptpaper = {...}` 装载。**只做接线**：供数据源、供 provider、
采证据（含 G-T13-8 的拒单原因），不含任何撮合/计价/判定逻辑。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

from rqalpha.core.events import EVENT
from rqalpha.interface import AbstractMod

from paper_trading.data.db_manager import DataDBManager
from paper_trading.integration.rq_engine import costs
from paper_trading.integration.rq_engine.provider import (FilePersistProvider,
                                                        PersistStateError)
from paper_trading.integration.rq_engine.store import RawDayBarStore

from paper_trading.integration.rq_engine.data_source import PaperDataSource


class PtPaperMod(AbstractMod):
    def __init__(self) -> None:
        self._mc = None
        self._env = None
        self._store = None
        self._provider = None
        self._counts: Counter = Counter()
        self._trades: list = []
        self._orders: list = []
        self._rejects: list = []
        self._cost_wrapped: list = []      # T38：过户费 overlay 装了哪几种合约
        self._cost_rate: float = 0.0

    # -- 事件采集（G-T13-7/8 的证据来源）----
    def _count(self, event):
        self._counts[str(getattr(event.event_type, "value", event.event_type)).upper()] += 1

    def _install_costs(self, event=None) -> None:
        """补过户费（裁决 5 / T55-R1 修复）。

        T52-R1：旧实现只挂 POST_BEFORE_TRADING——resume 跳过该事件 ⇒ **续跑进程
        每笔成交少收过户费**（实测 other_fees 0.0221→0.0）。修复：ptpaper
        priority 改 200（最后 start_up，框架 decider 已注册），start_up 里直接装；
        事件监听保留为幂等兜底（`_cost_wrapped` 防重复套层）。
        """
        if self._cost_wrapped:
            return
        self._cost_wrapped = costs.install_transfer_fee(self._env, self._cost_rate)

    def _on_trade(self, event):
        t = getattr(event, "trade", None)
        if t is None:
            return
        tc = getattr(t, "transaction_cost", 0)
        total = float(getattr(tc, "total", tc) or 0)
        # 费用**三分量**必须逐笔留：账本的 orders/fills 是佣金/印花税/过户费三列，
        # 只给一个合计数就得靠猜拆分（T47-G1：猜出来的费用拆分是账本腐烂的起点）。
        commission = float(getattr(t, "commission", 0) or 0)
        tax = float(getattr(t, "tax", 0) or 0)
        self._trades.append({
            "symbol": t.order_book_id, "datetime": str(t.datetime),
            "side": str(getattr(t.side, "value", t.side)).upper(),
            "price": round(float(t.last_price), 4), "volume": int(t.last_quantity),
            "cost": round(total, 4),
            "commission": round(commission, 6), "tax": round(tax, 6),
            "other_fees": round(total - commission - tax, 6),
            "position_effect": str(getattr(t.position_effect, "value",
                                           t.position_effect)).upper()})

    def _on_order(self, event):
        name = str(getattr(event.event_type, "value", event.event_type)).upper()
        o = getattr(event, "order", None)
        if o is None:
            # 前端风控挡下的单没有 Order 对象（environment.py:134-137 返回 None），
            # 原因只挂在 ORDER_CREATION_REJECT 事件 + user_system_log 上
            rec = {"event": name, "symbol": getattr(event, "order_book_id", "?"),
                   "status": "REJECTED_BY_VALIDATOR",
                   "reason": str(getattr(event, "reason", ""))}
            self._rejects.append(rec)
            self._orders.append(rec)
            return
        self._orders.append({
            "event": name, "symbol": o.order_book_id,
            "side": str(getattr(o.side, "value", o.side)).upper(),
            "quantity": int(o.quantity),
            "status": str(getattr(o.status, "value", o.status)).upper(),
            "message": str(o.message), "filled": int(o.filled_quantity)})

    # -- 生命周期 --
    def start_up(self, env, mod_config) -> None:
        self._mc = mod_config
        self._env = env
        self._cost_rate = float(getattr(mod_config, "transfer_fee_rate", 0.0) or 0.0)
        today = date.fromisoformat(mod_config.today)
        db = DataDBManager(mod_config.db)
        freq = str(getattr(mod_config, "frequency", "1d") or "1d")
        if freq == "1m":
            # M3b 分钟会话：5 分钟 bar（minute_bars period=5，raw 口径）驱动
            from .store import RawMinuteBarStore
            self._store = RawMinuteBarStore.from_data_db(
                db, list(mod_config.symbols), today=today,
                require_bands=bool(getattr(mod_config, "require_bands", True)))
        else:
            self._store = RawDayBarStore.from_data_db(
                db, list(mod_config.symbols), days=int(getattr(mod_config, "days", 260)),
                today=today, require_bands=bool(getattr(mod_config, "require_bands", True)),
                st_set=set(getattr(mod_config, "st_set", None) or ()),
                require_today_reference=bool(getattr(mod_config, "require_today_reference", True)))
        env.set_data_source(PaperDataSource(
            env.config.base, self._store,
            override_available_range=bool(getattr(mod_config, "override_available_range", True)),
            minute=(freq == "1m")))

        state_dir = getattr(mod_config, "state_dir", "") or ""
        if state_dir:
            self._provider = FilePersistProvider(state_dir)
            env.set_persist_provider(self._provider)
            if getattr(mod_config, "pre_load", False):   # 主动体检：坏状态先停线，别跑到一半才发现
                for k in self._provider.keys():
                    if self._provider.load(k) is None:
                        raise PersistStateError(
                            f"启动体检失败：{self._provider.restore_errors[-1]}")

        for name in ("BEFORE_TRADING", "BAR", "AFTER_TRADING", "SETTLEMENT",
                     "OPEN_AUCTION", "PRE_SETTLEMENT", "POST_SETTLEMENT"):
            try:
                env.event_bus.add_listener(getattr(EVENT, name), self._count)
            except Exception:                   # noqa: BLE001 - 事件名随版本变，缺了就少计数
                pass
        for name in [n for n in dir(EVENT) if n.startswith("ORDER_")]:
            try:
                env.event_bus.add_listener(getattr(EVENT, name), self._on_order)
            except Exception:                   # noqa: BLE001
                pass
        env.event_bus.add_listener(EVENT.TRADE, self._on_trade)
        if self._cost_rate > 0:
            # T55-R1：start_up 直接装（priority=200 时框架 decider 已在），
            # resume 跳过 BEFORE_TRADING 也不再漏装；事件监听保留为幂等兜底
            self._install_costs()
            env.event_bus.add_listener(EVENT.POST_BEFORE_TRADING, self._install_costs)

    def tear_down(self, code, exception=None):
        ev = getattr(self._mc, "evidence", "") if self._mc else ""
        if not ev:
            return
        Path(ev).parent.mkdir(parents=True, exist_ok=True)
        Path(ev).write_text(json.dumps({
            "event_counts": {k: v for k, v in self._counts.items()},
            "trades": self._trades,
            "orders": self._orders,
            "rejects": self._rejects,
            "store_stats": getattr(self._store, "stats", {}),
            "gate_rejected": getattr(self._store, "gate_rejected", {}),
            "provider": self._provider.snapshot() if self._provider else None,
            "cost": {"transfer_fee_rate": self._cost_rate,
                     "wrapped": self._cost_wrapped,
                     "deciders": (costs.overlay_stats(self._env)
                                  if self._env is not None else [])},
            "exit_code": str(getattr(code, "value", code)),
            "exception": str(exception) if exception else None,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    # -- 供 runner 读回 --
    @property
    def store(self):
        return self._store

    @property
    def provider(self):
        return self._provider


def load_mod():
    return PtPaperMod()
