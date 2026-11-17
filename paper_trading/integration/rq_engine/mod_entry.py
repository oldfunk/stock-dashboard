"""rqalpha mod 入口（模块名必须是 `rqalpha_mod_<mod_name>`，见 `mod/__init__.py:54`）。

`mod_name = "ptpaper"` → 本文件；`runner.py` 把本目录加入 `sys.path` 后用
`config.mod.ptpaper = {...}` 装载。**只做接线**：供数据源、供 provider、
采证据（含 G-T13-8 的拒单原因），不含任何撮合/计价/判定逻辑。
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path

from rqalpha.const import POSITION_DIRECTION
from rqalpha.core.events import EVENT
from rqalpha.interface import AbstractMod

from paper_trading.data.db_manager import DataDBManager
from paper_trading.integration.rq_engine import costs
from paper_trading.integration.rq_engine.provider import (FilePersistProvider,
                                                        PersistStateError)

logger = logging.getLogger(__name__)
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
        #: T73 期初对齐留痕（进 evidence，四场景判据读它）
        self._init_align: dict = {}
        self._frozen_align: dict = {}
        self._positions_landed: dict = {}

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

    # -- 期初对齐（T73）--
    def _check_init_positions(self, env, mod_config) -> dict:
        """核对"账本的期初持仓有没有被框架的解析静默吞掉"。

        取证（rqalpha 6.4.2，NEC 引擎 venv 实测）：
        `main.py:147 mod_handler.start_up()` → `:150 if not hasattr(env,"data_source")`
        → `:155 set_data_proxy(DataProxy(env.data_source,...))` → `:174 Portfolio(
        config.base.accounts, config.base.init_positions, ...)`。
        ⇒ **我们的 PaperDataSource 确实在 Portfolio 之前注册**，T73 要查的
        "data_source 不是我们的"这条**排除**；真因在 `utils/config.py:213/271`：
        `parse_init_positions` 只认 `"002192.XSHE:100"` 这种字符串，dict 进去
        **一律返回 `[]`**（`python -c` 实测），于是 Portfolio 每次都建了个空仓账户。

        本方法就是那道"再犯就停线"的哨兵：喂进去 N 只、解析后必须还是 N 只
        （除了框架标的表里根本没有的，例如开源 bundle 没有北交所 `.BJ`）。
        """
        from .runner import InitPositionError, reconcile_init_positions

        expected = dict(getattr(mod_config, "init_positions", None) or {})
        if not expected:
            return {"kept": [], "unsupported": [], "dropped_silently": []}
        parsed = list(getattr(env.config.base, "init_positions", None) or [])

        def known(ob: str) -> bool:
            """标的表里有没有这只（此刻还没有 data_proxy，只能问 data_source）。"""
            try:
                return next(iter(env.data_source.get_instruments(id_or_syms=[ob])),
                            None) is not None
            except Exception:                   # noqa: BLE001 - 问不动就当在，别静默丢仓
                return True

        out = reconcile_init_positions(expected, parsed, known)
        if out["unsupported"]:
            # 标的表里没有（北交所/未收录）：剔掉后照常开会话。这只仓框架本来就
            # 交易不了，runner 会以 INSTRUMENT_UNAVAILABLE 给出显式结论。
            env.config.base.init_positions = out["kept"]
            logger.warning("期初持仓中 %d 只不在框架标的表里，已剔除（框架交易不了）：%s",
                           len(out["unsupported"]),
                           [u["symbol"] for u in out["unsupported"]])
        if out["dropped_silently"]:
            raise InitPositionError(
                f"期初持仓被框架解析吞掉 {len(out['dropped_silently'])} 只："
                f"{out['dropped_silently']}（喂进去 {len(expected)} 只，解析后 "
                f"{len(parsed)} 只）——拒绝开会话：账本有仓、框架空仓时卖单会被"
                f"'仓位不足'冤枉拒、买单按虚高现金校验，两种都是钱类错误")
        return out

    def _framework_positions(self, env) -> dict:
        """框架账户里的持仓：`{order_book_id: (quantity, non_closable)}`。

        读 `stock_account._positions`（与 runner 的 `_snap` 同一口径）——不用
        `portfolio.get_position()`，它要先查合约的 account_type，对标的表里没有的
        代码会抛 `InstrumentNotFound`。
        """
        acct = getattr(getattr(env, "portfolio", None), "stock_account", None)
        if acct is None:
            return {}
        out = {}
        for ob, dirs in dict(getattr(acct, "_positions", {}) or {}).items():
            pos = dirs.get(POSITION_DIRECTION.LONG)
            if pos is not None:
                out[str(ob)] = {"quantity": int(pos.quantity),
                                "non_closable": int(getattr(pos, "_non_closable", 0))}
        return out

    def _check_positions_landed(self, env, mod_config) -> None:
        """框架账户与账本必须**逐只等量**：少仓或多仓都拒开这一跳。

        少仓＝账本有仓框架没有（卖单被冤枉拒）；多仓＝框架有账本没有的仓
        （按不存在的持仓卖出/按虚高市值算风险），两个方向都是钱类错误。
        多仓只可能来自"状态目录里躺着一本与账本脱钩的账户"（T81 P2-3）。

        **事件监听函数返回真值会让框架停止往下派发**（`core/events.py:43-46`
        `if listener(event): break`），所以本函数与 `_apply_t1_freeze` 一律返回 None。
        """
        from .runner import InitPositionError

        expected = {str(k): int(v) for k, v in
                    (dict(getattr(mod_config, "init_positions", None) or {})).items()
                    if int(v) > 0}
        for u in (self._init_align or {}).get("unsupported") or []:
            expected.pop(str(u["symbol"]), None)   # 已剔掉的不在框架里，不该报错
        have = self._framework_positions(env)
        short = [{"symbol": ob, "ledger": qty,
                  "framework": have.get(ob, {}).get("quantity", 0)}
                 for ob, qty in sorted(expected.items())
                 if have.get(ob, {}).get("quantity", 0) < qty]
        if short:
            raise InitPositionError(
                f"框架账户比账本少 {len(short)} 只仓：{short[:6]}——多半是旧的"
                f"会话状态目录在续跑一本空仓账户（11-05 现网：live/portfolio.state "
                f"positions 空、账本 7 只）。先停 cron、用 `ledger reset-state` 把"
                f"会话状态挪开，让下一跳从账本重建账户")
        self._positions_landed = {"checked": len(expected), "framework": len(have),
                                  "extra": []}
        extra = [{"symbol": ob, "framework": int(v.get("quantity", 0)),
                  "ledger": int(expected.get(ob, 0))}
                 for ob, v in sorted(have.items())
                 if int(v.get("quantity", 0)) > int(expected.get(ob, 0))]
        if extra:
            self._positions_landed["extra"] = extra
            raise InitPositionError(
                f"框架账户比账本多 {len(extra)} 只仓：{extra[:6]}——状态目录里的"
                f"账户与账本脱钩（多出的仓会被当成可卖持仓/虚高市值）。先停 cron、"
                f"用 `ledger reset-state --only rq_session_state` 把会话状态挪开，"
                f"让下一跳从账本重建账户")

    def _apply_t1_freeze(self, env, mod_config) -> None:
        """把账本的 T+1 冻结量补进框架（**必须在盘前释放之后**）。

        时机是被框架逼出来的，两跳各一个入口：
        * 新会话：`Position.before_trading()` 会把 `_non_closable` **清成 0**
          （`portfolio/position.py:248`，由 `Account._on_before_trading` 在
          PRE_BEFORE_TRADING 调，`account.py:122/364`）——这就是"昨天的仓今天可卖"。
          挂在它之前的任何对齐（11-05 的第一版挂在 POST_SYSTEM_INIT）都会被它抹掉；
        * 续跑跳：`executor._ensure_before_trading`（`core/executor.py:64-67`）
          当天不再发 PRE/POST_BEFORE_TRADING，冻结从 state 恢复（`non_closable`
          是持久化字段）——这条路上要在 POST_SYSTEM_RESTORED 之后补，别在盘前补。
        """
        from .runner import t1_freeze_plan

        frozen = dict(getattr(mod_config, "init_frozen", None) or {})
        acct = getattr(getattr(env, "portfolio", None), "stock_account", None)
        have = self._framework_positions(env)
        plan = t1_freeze_plan(frozen, have)
        for item in plan:
            if not item["changed"]:
                continue
            dirs = dict(getattr(acct, "_positions", {}) or {}).get(item["symbol"])
            p = dirs.get(POSITION_DIRECTION.LONG) if dirs else None
            if p is None:
                continue
            p._non_closable = item["to"]            # noqa: SLF001 - 框架无公开 API
        self._frozen_align = {
            "checked": len(frozen), "applied": plan,
            "framework_positions": len(have),
            "closable": {i["symbol"]: int(i["quantity"]) - int(i["to"]) for i in plan},
        }

    # -- 生命周期 --

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

        # T73（11-05 P0 的最后一环）：期初持仓在框架解析后还剩多少——
        # 必须在 `main.py:174` 建 Portfolio 之前把不该丢的丢掉、把静默丢的喊出来。
        self._init_align = self._check_init_positions(env, mod_config)

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
        # M3b T+1 对齐（2026-11-05，T73 修正时机）：`init_positions` 导入的持仓框架
        # 视为**全额可卖**（框架只给"当天买入"加 `_non_closable`，
        # `position_model.py:176-177`），账本 available<total 的那部分必须补成不可平仓。
        # 旧写法在本方法里直接读 `env.portfolio`——但 Portfolio 是 `main.py:174` 建的，
        # `start_up` 是 `main.py:147`，此刻**还没有 portfolio**，异常被 `except` 咽成
        # 一条 warning ⇒ T+1 对齐从未生效（T74 前置审查 P1 的第二条，实测确认）。
        # 就算挪到 POST_SYSTEM_INIT 也不够：`Position.before_trading()` 在
        # PRE_BEFORE_TRADING 里把 `_non_closable` 清零（昨天的仓今天可卖，
        # `portfolio/position.py:248`），冻结必须补在它**之后**——见 `_apply_t1_freeze`。
        env.event_bus.add_listener(
            EVENT.POST_SYSTEM_INIT,
            lambda e: self._check_positions_landed(env, mod_config))
        # 少仓校验还要挂第二个时刻：续跑跳的账户是 `persist_helper.restore`
        # （main.py:210）换上去的，比 POST_SYSTEM_INIT（main.py:178）**晚**——
        # 只挂前者的话，"状态目录里躺着一本与账本脱钩的账户"会被刚按账本
        # 建好的临时 Portfolio 挡过去（四场景 e2e 的 guard 实测就是这么漏的）。
        env.event_bus.add_listener(
            EVENT.POST_SYSTEM_RESTORED,
            lambda e: self._check_positions_landed(env, mod_config))
        registered = []
        for ev_name in ("POST_BEFORE_TRADING", "POST_SYSTEM_RESTORED"):
            ev = getattr(EVENT, ev_name, None)
            if ev is None:
                continue
            env.event_bus.add_listener(ev, lambda e: self._apply_t1_freeze(env, mod_config))
            registered.append(ev_name)
        if dict(getattr(mod_config, "init_frozen", None) or {}) and not registered:
            # 有要冻的仓却没有能挂对齐的事件 ⇒ 不许"先跑着再说"：框架会当成
            # 全额可卖，T+0 卖出合规风险由这一跳承担（T72 审的正是这条语义）。
            from .runner import InitPositionError

            raise InitPositionError(
                f"EVENT 里没有 POST_BEFORE_TRADING/POST_SYSTEM_RESTORED"
                f"（框架版本变了？），T+1 冻结无处可挂："
                f"{sorted(mod_config.init_frozen)}")

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
            # T73：期初对齐留痕（四场景判据 + 事后审计都读这个，不看日志）
            "init_align": self._init_align,
            "frozen_align": self._frozen_align,
            "positions_landed": self._positions_landed,
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
