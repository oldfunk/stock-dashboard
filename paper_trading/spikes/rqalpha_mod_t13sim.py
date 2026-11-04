# -*- coding: utf-8 -*-
"""T13 常驻模拟线 Mod（原型，非产品代码）。

它回答 T13 的三个取证问题，全部用 rqalpha 官方扩展点，不改框架源码：

1. 事件源/撮合器怎么来      -> `sys_simulation` mod 自己注册（mod.py:66-74），
   `run_type=p` 同样走这条路径（mod.py:35 只对 LIVE_TRADING 早退）。
2. 新鲜数据怎么灌进常驻线    -> `BaseDataSource.register_day_bar_store`
   (data/base_data_source/data_source.py:143) 热替换 CS 日线 store；
   日历/合约/分红/因子/停牌/ST 仍取自 bundle。
3. 进程每天重启怎么续跑      -> rqalpha 6.4.2 **core 里没有 AbstractPersistProvider
   的实现**（只有 interface.py:643 的抽象类），`persist: true` 会在
   main.py:77-79 直接抛 "Missing persist provider"。本文件自带一个
   文件态 provider + 会话闸（幂等/配置指纹），这就是 M2 要落地的东西。

口径：`daily_bars` 存的是**前复权**（见 report-t13 §4 实测），rqalpha 的
DayBarStore 契约要的是**原始价**（bundle `stocks.h5` 即原始价，
`data/base_data_source/adjust.py` 再按 ex_cum_factor 复权）。所以 store
里先把 qfq 还原成 raw，再交给 rqalpha。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import h5py
import numpy as np

from rqalpha.const import INSTRUMENT_TYPE, MARKET
from rqalpha.core.events import EVENT
from rqalpha.data.base_data_source.data_source import BaseDataSource
from rqalpha.interface import AbstractMod, AbstractPersistProvider

BAR_DTYPE = np.dtype([
    ("datetime", np.int64),
    ("open", np.float64),
    ("close", np.float64),
    ("high", np.float64),
    ("low", np.float64),
    ("volume", np.float64),
    ("total_turnover", np.float64),
    ("prev_close", np.float64),
    ("limit_up", np.float64),
    ("limit_down", np.float64),
])

TICK_SIZE = 0.01


def to_obid(symbol: str) -> str:
    if symbol[0] in "69":
        return f"{symbol}.XSHG"
    if symbol[0] in "48":
        return f"{symbol}.BJ"
    return f"{symbol}.XSHE"


def to_symbol(order_book_id: str) -> str:
    return order_book_id.split(".")[0]


def d2i(d: date) -> int:
    """rqalpha 的 bar datetime 编码：YYYYMMDD000000（datetime_func.py:35-41）。"""
    return d.year * 10_000_000_000 + d.month * 100_000_000 + d.day * 1_000_000


def round2(x: float) -> float:
    """涨跌停价 = 前收 ×(1±band) 四舍五入到最小变动单位 0.01（AGENTS 合规表 #7）。"""
    return math.floor(x * 100 + 0.5) / 100


def band_of(symbol: str, is_st: bool = False) -> float:
    """主板 10% / 创业板·科创板 20% / 北交所 30%（AGENTS 合规表 #4/#5/#6）。

    ST **不降档**：主板 ST 自 2026-07-06 起由 5% 放宽至 10%，创业/科创 ST 同幅
    （AGENTS 硬规则 2 明确"不得沿用 ST=5% 旧口径"）。参数保留只为显式说明此事。
    """
    del is_st  # 新规下无特判；留着是为了调用点必须显式表态（Hermes T33 P0）
    if symbol[0] in "48":
        return 0.30
    if symbol[:2] in ("30", "68"):
        return 0.20
    return 0.10


class SqliteDayBarStore:
    """AbstractDayBarStore 实现：我们的 daily_bars（qfq）→ 原始价 bar 数组。"""

    def __init__(self, db_path: str, bundle_dir: str, today: date,
                 caliber: str = "raw", bands: bool = True):
        self.today = today
        self.caliber = caliber
        self.bands = bands
        self._bundle_closes: dict[str, dict[str, float]] = {}
        self._bundle_dir = bundle_dir
        self._bars: dict[str, np.ndarray] = {}
        self.stats: dict[str, dict] = {}
        self._load(db_path)

    # ---- 数据装载 -------------------------------------------------------
    def _load(self, db_path: str) -> None:
        rows: dict[str, list] = {}
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for sym, ts, o, h, l, c, v in conn.execute(
                "select symbol, timestamp, open, high, low, close, volume from daily_bars"
            ):
                d = date.fromisoformat(str(ts)[:10])
                if d > self.today:
                    continue
                rows.setdefault(sym, []).append((d, (float(o), float(h), float(l), float(c), float(v))))
        finally:
            conn.close()

        for sym, series in rows.items():
            series.sort()
            self._bars[to_obid(sym)] = self._build(sym, series)

        days = sorted({d for s in rows.values() for d, _ in s})
        self.first_date = days[0] if days else self.today
        self.last_date = days[-1] if days else self.today

    def _bundle_map(self, obid: str) -> dict[str, float]:
        if obid not in self._bundle_closes:
            out = {}
            p = Path(self._bundle_dir) / "stocks.h5"
            if p.exists():
                with h5py.File(p, "r") as h5:
                    if obid in h5:
                        arr = h5[obid][:]
                        for dt_int, close in zip(arr["datetime"], arr["close"]):
                            s = str(int(dt_int))
                            out[f"{s[:4]}-{s[4:6]}-{s[6:8]}"] = float(close)
            self._bundle_closes[obid] = out
        return self._bundle_closes[obid]

    def _scales(self, sym: str, bmap: dict[str, float], series: list):
        """qfq→raw 的尺度函数 scale(t)：原始价 = qfq 价 × scale(t)。

        scale 只能从 bundle 的复权因子反推（bundle 覆盖到的日子），
        超出覆盖的新鲜日沿用它之前最后一段的 scale——**隐含"其间没有除权除息"
        的假设**，这正是 M2 必须补的数据缺口（report-t13 §4.3）。
        """
        obid = to_obid(sym)
        if self.caliber == "qfq" or not bmap:
            return {d: 1.0 for d, _ in series}, 0, obid
        steps = []
        known = [(d, bmap[d.isoformat()] / c) for d, (_, _, _, c, _) in series
                 if d.isoformat() in bmap and c > 0]
        if not known:
            return {d: 1.0 for d, _ in series}, 0, obid
        cur = known[0][1]
        kmap = {}
        prev = None
        for d, s in known:
            if prev is None or abs(s / prev - 1) > 0.005:
                steps.append((d, s))
            kmap[d] = s
            prev = s
        out = {}
        si = 0
        for d, _ in series:
            while si + 1 < len(steps) and steps[si + 1][0] <= d:
                si += 1
            out[d] = steps[si][1]
        return out, len(steps), obid

    def _build(self, sym: str, series: list) -> np.ndarray:
        bmap = self._bundle_map(to_obid(sym))
        scales, n_steps, obid = self._scales(sym, bmap, series)
        n = len(series)
        arr = np.zeros(n, dtype=BAR_DTYPE)
        fresh = 0
        last_known = max(bmap) if bmap else ""
        for i, (d, (o, h, l, c, v)) in enumerate(series):
            s = scales[d]
            if d.isoformat() > last_known and last_known:
                fresh += 1
            arr[i]["datetime"] = d2i(d)
            arr[i]["open"] = round2(o * s)
            arr[i]["close"] = round2(c * s)
            arr[i]["high"] = round2(h * s)
            arr[i]["low"] = round2(l * s)
            arr[i]["volume"] = v
            # daily_bars 无成交额字段：VWAP 撮合需要 total_turnover，这里只能用
            # close×volume 近似（report-t13 §4.2：vwap matching_type 在我们的数据上不可用）
            arr[i]["total_turnover"] = round2(c * s) * v
            if i > 0:
                pc = arr[i - 1]["close"]
                arr[i]["prev_close"] = pc
                if self.bands:
                    b = band_of(sym)
                    arr[i]["limit_up"] = round2(pc * (1 + b))
                    arr[i]["limit_down"] = round2(pc * (1 - b))
                else:
                    arr[i]["limit_up"] = np.nan
                    arr[i]["limit_down"] = np.nan
            else:
                arr[i]["limit_up"] = np.nan
                arr[i]["limit_down"] = np.nan
                arr[i]["prev_close"] = np.nan
        self.stats[obid] = {"symbol": sym, "bars": n, "fresh_bars": fresh,
                            "qfq_scale_steps": n_steps,
                            "scale_last": round(scales[series[-1][0]], 6)}
        return arr

    # ---- AbstractDayBarStore -------------------------------------------
    def get_bars(self, order_book_id: str) -> np.ndarray:
        return self._bars.get(order_book_id, np.empty(0, dtype=BAR_DTYPE))

    def get_date_range(self, order_book_id: str):
        bars = self._bars.get(order_book_id)
        if bars is None or len(bars) == 0:
            return d2i(self.today), d2i(self.today)
        return int(bars[0]["datetime"]), int(bars[-1]["datetime"])


class T13DataSource(BaseDataSource):
    """bundle 静态元数据 + 我们的日线 store（热替换 CS）。

    `override_available=False` 用来复现"静默截窗"缺陷：基类
    `available_data_range` 只问 INDX store（data_source.py:406-414），
    于是 bundle 结束日之后的"今日"会被 main.py:54-55 悄悄夹掉。
    """

    def __init__(self, base_config, store: SqliteDayBarStore, override_available: bool = True):
        super().__init__(base_config, market=MARKET.CN)
        self._t13_store = store
        self._override_available = override_available
        self.register_day_bar_store(INSTRUMENT_TYPE.CS, store)

    def available_data_range(self, frequency):
        if not self._override_available:
            return super().available_data_range(frequency)
        return self._t13_store.first_date, self._t13_store.today


class FilePersistProvider(AbstractPersistProvider):
    """rqalpha core 缺失的那块拼图：一 key 一文件的态存储。"""

    def __init__(self, state_dir: str):
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def store(self, key, value):
        p = self._dir / f"{key}.bin"
        tmp = self._dir / f"{key}.bin.tmp"
        tmp.write_bytes(value)
        os.replace(tmp, p)

    def load(self, key):
        p = self._dir / f"{key}.bin"
        return p.read_bytes() if p.exists() else None

    def should_resume(self):
        return True

    def should_run_init(self):
        return True

    def keys(self):
        return sorted(p.stem for p in self._dir.glob("*.bin"))


class T13SimMod(AbstractMod):
    """把上面三件套接进 rqalpha 的 mod 生命周期。"""

    def __init__(self):
        self._mc = None
        self._counts = Counter()
        self._trades = []
        self._orders = []
        self._store = None

    # -- gates -----------------------------------------------------------
    def _session_gate(self, provider: FilePersistProvider, fingerprint: str, today: date):
        """幂等 + 配置指纹：同一天重复跑/指纹漂移 -> 拒（fail-closed，AGENTS 纪律 4）。"""
        meta_p = self._meta_path(provider)
        if not meta_p.exists():
            return
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        if meta.get("fingerprint") != fingerprint:
            raise RuntimeError(
                "T13 GATE: 断点状态与本会话配置指纹不一致（存档 "
                f"{meta.get('fingerprint')} ≠ 当前 {fingerprint}）——"
                "续跑会把两个口径的账本拼在一起，拒绝启动")
        if meta.get("last_day", "") >= today.isoformat():
            raise RuntimeError(
                f"T13 GATE: 状态已推进到 {meta['last_day']}，本次请求 {today} 不晚于它；"
                "同日重跑会重复下单（rqalpha 的 resume 只跳 before_trading，"
                "handle_bar 会再执行一次）。确需重跑请 --force")

    @staticmethod
    def _meta_path(provider: FilePersistProvider) -> Path:
        return Path(str(provider._dir)) / "meta.json"

    # -- listeners -------------------------------------------------------
    def _on(self, event):
        self._counts[event.event_type] += 1

    def _on_trade(self, event):
        t = getattr(event, "trade", None)
        if t is not None:
            tc = getattr(t, "transaction_cost", 0)
            self._trades.append({
                "symbol": t.order_book_id, "datetime": str(t.datetime),
                "side": str(getattr(t.side, "value", t.side)).upper(),
                "price": round(float(t.last_price), 4),
                "volume": int(t.last_quantity),
                "cost": round(float(getattr(tc, "total", tc) or 0), 4),
                "position_effect": str(getattr(t.position_effect, "value", t.position_effect)).upper()})

    def _on_order(self, event):
        name = str(getattr(event.event_type, "value", event.event_type)).upper()
        o = getattr(event, "order", None)
        if o is None:
            # 前端风控挡下的单没有 Order 对象（environment.py:134-137 返回 None），
            # 拒单原因只挂在 ORDER_CREATION_REJECT 事件 + user_system_log 上
            self._orders.append({"event": name, "symbol": getattr(event, "order_book_id", "?"),
                                 "side": "-", "quantity": 0, "status": "REJECTED_BY_VALIDATOR",
                                 "message": str(getattr(event, "reason", "")), "filled": 0})
            return
        self._orders.append({
            "event": name,
            "symbol": o.order_book_id,
            "side": str(getattr(o.side, "value", o.side)).upper(),
            "quantity": int(o.quantity),
            "status": str(getattr(o.status, "value", o.status)).upper(),
            "message": str(o.message),
            "filled": int(o.filled_quantity),
        })

    def start_up(self, env, mod_config):
        self._mc = mod_config
        today = date.fromisoformat(mod_config.today)
        self._store = SqliteDayBarStore(mod_config.db, mod_config.bundle, today,
                                       caliber=getattr(mod_config, "caliber", "raw"),
                                       bands=getattr(mod_config, "bands", True))
        env.set_data_source(T13DataSource(env.config.base, self._store,
                                          getattr(mod_config, "override_available", True)))
        state_dir = getattr(mod_config, "state_dir", "") or ""
        self._provider = None
        if state_dir:
            self._provider = FilePersistProvider(state_dir)
            fp = getattr(mod_config, "fingerprint", "")
            if not getattr(mod_config, "force", False):
                self._session_gate(self._provider, fp, today)
            env.set_persist_provider(self._provider)
        for name in ("BEFORE_TRADING", "BAR", "AFTER_TRADING", "SETTLEMENT", "OPEN_AUCTION",
                     "PRE_SETTLEMENT", "POST_SETTLEMENT", "ORDER_CREATION_PASS", "ORDER_TRADED"):
            try:
                env.event_bus.add_listener(getattr(EVENT, name), self._on)
            except Exception:
                pass
        for name in [n for n in dir(EVENT) if n.startswith("ORDER_")]:
            try:
                env.event_bus.add_listener(getattr(EVENT, name), self._on_order)
            except Exception:
                pass
        env.event_bus.add_listener(EVENT.TRADE, self._on_trade)

    def tear_down(self, code, exception=None):
        ok = exception is None and str(getattr(code, "value", code)).endswith("EXIT_SUCCESS")
        ev = getattr(self._mc, "evidence", "") or ""
        if ev:
            counts = {str(getattr(k, "value", k)).upper(): v for k, v in self._counts.items()}
            Path(ev).write_text(json.dumps({
                "event_counts": counts,
                "trades": self._trades,
                "orders": self._orders,
                "store_stats": self._store.stats,
                "exit_code": str(getattr(code, "value", code)),
                "exception": str(exception) if exception else None,
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        if self._provider is not None and ok:
            self._meta_path(self._provider).write_text(json.dumps({
                "last_day": self._mc.today,
                "fingerprint": getattr(self._mc, "fingerprint", ""),
                "written_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False), encoding="utf-8")


def load_mod():
    return T13SimMod()
