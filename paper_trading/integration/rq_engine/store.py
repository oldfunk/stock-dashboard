"""原始价 DayBarStore：把 `daily_bars_raw` 喂成 rqalpha 的 bar 结构化数组。

契约（实测 rqalpha 6.4.2 `data/base_data_source/storages.py:226-238` +
`data_source.py:230-243`）：`get_bars(order_book_id)` 返回按 `datetime` 升序的
numpy 结构化数组，字段至少 `datetime/open/close/high/low/volume/total_turnover`，
日线撮合与涨跌停还要 `limit_up/limit_down`（bundle 的 `SecuritiesDayBarStore`
就多这两列）。rqalpha 在**读的时候**按 `ex_cum_factor` 复权（`adjust.py:30-77`），
所以 store 里必须是**原始价**——这正是 T31 管道的产出。

鸭子类型：本模块不 import rqalpha（产品 venv 不装它），
`BaseDataSource.register_day_bar_store` 只要求 `get_bars/get_date_range` 存在。
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

import numpy as np

from paper_trading.utils import get_logger
from .bands import limit_prices

logger = get_logger(__name__)

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

#: G-T13-6 的兄弟检查：qfq 的 OHLC 拼 raw 的 close 会出现这种不自洽
OHLC_ORDER = ("high", "low")


class BandMissingError(RuntimeError):
    """G-T13-3：band/prev_close 供不齐 ⇒ 拒供该日（rqalpha 缺字段会 fail-open）。"""


def to_order_book_id(symbol: str) -> str:
    """6 位码 → rqalpha `order_book_id`（口径唯一，见 `utils/markets.py`）。

    旧写法按首字符判市：`920156`（北交所新段）被判成 `920156.XSHG`——
    挂错交易所，撮合要么找不到标的、要么拿错 band/日历（T60 现网实据）。
    """
    from paper_trading.utils import markets

    return markets.order_book_id(symbol)


def to_symbol(order_book_id: str) -> str:
    from paper_trading.utils import markets

    return markets.from_order_book_id(order_book_id)


def _d2i(d: date) -> int:
    """rqalpha 的 bar datetime 编码 YYYYMMDD000000（`datetime_func.py:35-41`）。"""
    return d.year * 10_000_000_000 + d.month * 100_000_000 + d.day * 1_000_000


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)[:10]).date()


class RawDayBarStore:
    """`AbstractDayBarStore` 的鸭子实现。

    `require_bands=True`（默认）：只要**最新一天**或**任何中间日**缺
    `prev_close/limit_up/limit_down` 就抛 `BandMissingError`——不许带病供数。
    序列首根例外（没有前收盘可用），它一旦被当成"今日"也会被最新日检查拦住。
    """

    def __init__(self, bars_by_symbol: dict[str, list], *, today: Optional[date] = None,
                 require_bands: bool = True, is_st: Optional[Iterable[str]] = None,
                 st_set: Optional[set] = None) -> None:
        del is_st  # 新规下 ST 与同板块同幅，不需要标记；参数保留逼调用方表态
        self.today = _as_date(today) if today else None
        self.require_bands = require_bands
        self.st_set = set(st_set or ())
        self._bars: dict[str, np.ndarray] = {}
        self.stats: dict[str, dict] = {}
        self.gate_rejected: dict[str, str] = {}      # 被参照闸挡下的标的 → 原因
        self._build(bars_by_symbol)

    @classmethod
    def from_data_db(cls, db, symbols, *, days: int = 260, today=None,
                     require_bands: bool = True, st_set: Optional[set] = None,
                     require_today_reference: bool = True):
        """从 `DataDBManager` 的 raw 表读（引擎唯一允许的数据源）。

        `require_today_reference`（T50-C）：**今天这根没有独立参照的标的直接不供**，
        而不是降级喂进去——母库 T 日 `close_raw` 为 NULL 且盘后快照也没对上的日子，
        引擎就该"缺数据不下单"。被挡的标的记在 `gate_rejected` 里，随 evidence 出，
        这样"为什么今天没成交"是可审计的，不是一句"无 bar"。
        """
        from paper_trading.data.raw_feed import today_reference_state

        data = {}
        rejected = {}
        today_d = _as_date(today) if today is not None else None
        for s in symbols:
            sym = to_symbol(s) if "." in str(s) else str(s)
            if require_today_reference and today_d is not None:
                st = today_reference_state(db, sym, today=today_d)
                if st["state"] == "unchecked":
                    rejected[sym] = "today_bar_unchecked"
                    continue
            rows = db.get_raw_bars(sym, limit=days)
            if rows:
                data[str(s)] = rows
        store = cls(data, today=today, require_bands=require_bands, st_set=st_set)
        store.gate_rejected = rejected
        if rejected:
            logger.warning("G-T13-1-C 当日无独立参照，拒供：%s", rejected)
        return store

    # ---- 构建 ----------------------------------------------------------
    def _build(self, bars_by_symbol: dict[str, list]) -> None:
        problems: list[str] = []
        for symbol, rows in bars_by_symbol.items():
            obid = to_order_book_id(symbol)
            series = sorted((_as_date(r.timestamp if hasattr(r, "timestamp") else r["timestamp"]),
                             r) for r in rows)
            if self.today is not None:
                series = [(d, r) for d, r in series if d <= self.today]
            if not series:
                continue
            arr = np.zeros(len(series), dtype=BAR_DTYPE)
            # 这三列 + 成交额必须显式 NaN 起步：np.zeros 会留下 0.0，
            # 而 rqalpha 的 is_valid_price 把 0 当无效 ⇒ 涨跌停闸静默失效
            for col in ("prev_close", "limit_up", "limit_down", "total_turnover"):
                arr[col] = math.nan
            for i, (d, r) in enumerate(series):
                o, h, l, c = (float(r.open), float(r.high), float(r.low), float(r.close))
                if not all(x > 0 and math.isfinite(x) for x in (o, h, l, c)):
                    problems.append(f"{symbol} {d}：OHLC 存在非法值")
                    continue
                if not (h >= max(o, c) and l <= min(o, c) and h >= l):
                    problems.append(f"{symbol} {d}：OHLC 不自洽（混合口径特征）")
                    continue
                arr[i]["datetime"] = _d2i(d)
                arr[i]["open"], arr[i]["close"] = o, c
                arr[i]["high"], arr[i]["low"] = h, l
                arr[i]["volume"] = float(r.volume or 0)
                amt = getattr(r, "amount", None)
                arr[i]["total_turnover"] = float(amt) if amt else math.nan
                if i > 0:
                    pc = arr[i - 1]["close"]
                    up, down = limit_prices(pc, symbol, is_st=(symbol in self.st_set))
                    arr[i]["prev_close"] = pc
                    arr[i]["limit_up"], arr[i]["limit_down"] = up, down
            arr = arr[arr["datetime"] > 0]
            self._bars[obid] = arr
            self.stats[obid] = {
                "symbol": symbol, "bars": int(len(arr)),
                "first": str(_date_from_int(arr["datetime"][0])) if len(arr) else None,
                "last": str(_date_from_int(arr["datetime"][-1])) if len(arr) else None,
                "bands_ok": int(np.isfinite(arr["limit_up"]).sum()) if len(arr) else 0,
                "turnover_ok": int(np.isfinite(arr["total_turnover"]).sum()) if len(arr) else 0,
            }
            if self.require_bands:
                # 最新一天必须有 band（引擎今天就要下单）
                if len(arr) and not math.isfinite(arr["limit_up"][-1]):
                    problems.append(
                        f"{symbol} 最新日 {self.stats[obid]['last']} 无涨跌停带"
                        f"（缺前收盘？）——rqalpha 会 fail-open，拒供")
                # 中间日也不许断带
                miss = int(np.count_nonzero(~np.isfinite(arr["limit_up"][1:]))) if len(arr) > 1 else 0
                if miss:
                    problems.append(f"{symbol} 序列中段缺带 {miss} 根")
        if problems and self.require_bands:
            raise BandMissingError("G-T13-3 band/OHLC 门禁不过：" + "；".join(problems[:8]))
        self.problems = problems

    # ---- AbstractDayBarStore 接口 ------------------------------------
    def get_bars(self, order_book_id: str) -> np.ndarray:
        return self._bars.get(order_book_id, np.empty(0, dtype=BAR_DTYPE))

    def get_date_range(self, order_book_id: str):
        bars = self._bars.get(order_book_id)
        if bars is None or len(bars) == 0:
            return _d2i(self.today or date.today()), _d2i(self.today or date.today())
        return int(bars["datetime"][0]), int(bars["datetime"][-1])

    def order_book_ids(self) -> list:
        return sorted(self._bars)

    def first_date(self) -> Optional[date]:
        """全池最早一根 bar 的日期（`available_data_range` 左端用）。"""
        days = [v["first"] for v in self.stats.values() if v.get("first")]
        return date.fromisoformat(min(days)) if days else self.today


def _date_from_int(v: int) -> date:
    s = str(int(v))
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))

# ---------------------------------------------------------------------------
# M3b：分钟 store（5 分钟 bar 喂 rqalpha 1m 频率；主 AI 2026-10-30，一天迁移令）
# ---------------------------------------------------------------------------
BAR_DTYPE_MINUTE = np.dtype([
    ("datetime", np.int64),
    ("open", np.float64),
    ("high", np.float64),
    ("low", np.float64),
    ("close", np.float64),
    ("volume", np.float64),
    ("total_turnover", np.float64),
    ("prev_close", np.float64),
    ("limit_up", np.float64),
    ("limit_down", np.float64),
])


def _dt2i_minute(ts: str) -> int:
    """'YYYY-MM-DDTHH:MM:SS' → rqalpha 分钟编码 YYYYMMDDHHMM00。"""
    s = str(ts).replace("T", " ")[:16]
    d = s[:10].replace("-", "")
    hm = s[11:16].replace(":", "")
    return int(d) * 1_000_000 + int(hm) * 100


def aggregate_day_from_minutes(bars, day_key: str, symbol: str) -> tuple:
    """把某日的分钟 bar 聚合成等价日线；返回 `(row, note)`。

    为什么要这条（2026-11-05）：`init_positions` 导入期初持仓时框架要按
    `prev_date` 取一根 **1d** bar 当成本价（`portfolio/account.py:90`），而开源
    bundle 的日线落后到月末 ⇒ `BaseDataSource` 给 NaN ⇒ 那只仓**静默导入失败**
    （框架空仓、卖单被"仓位不足"拒，就是 11-05 的 P0）。分钟库里有当日 48 根
    5 分钟 bar ⇒ 自己聚合出等价日线（OHLC 首开/最高/最低/末收，量额求和）。

    `prev_close/limit_up` 非有限时（T74 审查 P2 → T75 落地）**回退**：
    `prev_close(D)` 的定义就是"上一交易日收盘"，即本序列里比 D 更近那一天的
    末根 close；band 用同一个 `bands.limit_prices` 现算（不留第二份档表）。
    退无可退才返回 `(None, note)`——让调用方明确报错，而不是拿 NaN 建一仓
    看不见的成本（band 是撮合判涨跌停的唯一依据 `matcher/base.py:207-218`）。

    note 非空 ⇒ 走了回退/失败路径，调用方要把它写进 `store.problems` 留痕。
    """
    sel = [b for b in bars if str(int(b["datetime"]))[:8] == str(day_key)]
    if not sel:
        return None, f"1d 聚合失败：{symbol} {day_key} 当日无分钟 bar"
    row = np.zeros(1, dtype=bars.dtype)[0]
    row["datetime"] = int(day_key) * 1_000_000
    row["open"] = float(sel[0]["open"])
    row["high"] = float(max(b["high"] for b in sel))
    row["low"] = float(min(b["low"] for b in sel))
    row["close"] = float(sel[-1]["close"])
    row["volume"] = float(sum(b["volume"] for b in sel))
    row["total_turnover"] = float(sum(
        b["total_turnover"] for b in sel
        if np.isfinite(b["total_turnover"]))) or np.nan
    pc = float(sel[0]["prev_close"])
    lu = float(sel[0]["limit_up"])
    if np.isfinite(pc) and np.isfinite(lu) and pc > 0:
        row["prev_close"] = pc
        row["limit_up"] = lu
        row["limit_down"] = float(sel[0]["limit_down"])
        return row, ""
    earlier = [b for b in bars if str(int(b["datetime"]))[:8] < str(day_key)]
    if earlier:
        last_day = str(int(earlier[-1]["datetime"]))[:8]
        prev = [b for b in earlier if str(int(b["datetime"]))[:8] == last_day]
        cand = float(prev[-1]["close"]) if prev else float("nan")
        if np.isfinite(cand) and cand > 0:
            row["prev_close"] = cand
            row["limit_up"], row["limit_down"] = limit_prices(cand, symbol)
            return row, (f"1d 聚合回退：{symbol} {day_key} 日线 raw 的 "
                         f"prev_close/带非有限（pc={pc} limit_up={lu}），"
                         f"改用上一交易日({last_day})分钟收盘 {cand}")
    return None, (f"1d 聚合失败：{symbol} {day_key} prev_close/带非有限"
                  f"（pc={pc} limit_up={lu}）且无上一交易日可退")


class RawMinuteBarStore:
    """`AbstractDayBarStore` 的分钟版鸭子实现（接口同名 get_bars）。

    5 分钟 bar（`minute_bars` period=5，raw 口径）→ rqalpha `frequency=1m`。
    rqalpha 1m 事件源按**日历逐分钟**发 BAR 事件（simulation_event_source
    `_get_stock_trading_minutes`）；无 bar 的分钟 `bar_dict` 为空，策略侧
    自行跳过（`bar is None`）。涨跌停带按日供给（同日所有分钟同一带，
    prev_close = 该日日线 raw close 的前一日值）——带缺失仍 fail-closed
    拒供（G-T13-3 口径不变）。
    """

    def __init__(self, bars_by_symbol: dict[str, list], *, today=None,
                 require_bands: bool = True,
                 day_prev_close: Optional[dict] = None):
        self.today = _as_date(today) if today is not None else None
        self.require_bands = require_bands
        self._bars: dict[str, np.ndarray] = {}
        self.stats: dict[str, dict] = {}
        self.gate_rejected: dict[str, str] = {}
        self._build(bars_by_symbol, day_prev_close or {})
        self.problems: list[str] = []

    @classmethod
    def from_data_db(cls, db, symbols, *, today=None, require_bands: bool = True,
                     require_prev_close: bool = True):
        """从 `minute_bars`（period=5）读当日及历史分钟线。

        prev_close/带：从 `daily_bars_raw` 日线序列取（每分钟 bar 的带 =
        所属交易日的 [前日 close × 档位]）；日线 raw 缺该日则该日分钟拒供。

        `require_prev_close`（T60，默认开）：**当日那根的 prev_close 必须来自
        "上一个交易日"的 raw close**，不许拿更早的收盘凑。旧写法在 raw 缺 T-1 时
        退回"最近可得日的 close"——11-03 现网 raw 只到 10-30（15:40 采集早于源
        发布当日 bar），当天涨跌停带就会用**两个交易日前**的收盘算，band 是撮合
        判涨跌停的唯一依据（`matcher/base.py:207-218`），错档=错判涨跌停，
        而且从日志上完全看不出来。缺 T-1 的标的现在记进 `gate_rejected` 并拒供。
        """
        from paper_trading.utils.trading_calendar import is_trading_day

        today_d = _as_date(today) if today is not None else date.today()
        prev_d = None
        if require_prev_close and today_d is not None:
            d = today_d
            for _ in range(20):
                d = d - timedelta(days=1)
                if is_trading_day(d):
                    prev_d = d
                    break
        data: dict[str, list] = {}
        day_pc: dict[str, dict] = {}
        rejected: dict[str, str] = {}
        for s in symbols:
            sym = to_symbol(s) if "." in str(s) else str(s)
            mrows = db.get_minute_bars(sym, period="5", limit=100000)
            if not mrows:
                continue
            # 日线 raw 序列 → 每日 prev_close（同日分钟共用）
            drows = db.get_raw_bars(sym, limit=400) or []
            if require_prev_close and prev_d is not None and                     prev_d.isoformat() not in {str(r.timestamp)[:10] for r in drows}:
                have = max((str(r.timestamp)[:10] for r in drows), default="无")
                rejected[sym] = (f"prev_close_missing: 当日带需要 {prev_d.isoformat()}"
                                 f" 的 raw 收盘，表里最新只到 {have}")
                continue
            pc_by_day: dict[str, tuple] = {}
            closes = [(str(r.timestamp)[:10], float(r.close)) for r in drows]
            closes.sort()
            for i, (d, c) in enumerate(closes):
                if i > 0:
                    pc_by_day[d] = (closes[i - 1][1], c)
            day_pc[str(s)] = {k: v for k, v in pc_by_day.items()}
            data[str(s)] = mrows
        store = cls(data, today=today_d, require_bands=require_bands,
                    day_prev_close=day_pc)
        store.gate_rejected = rejected
        if rejected:
            logger.warning("T60 当日 prev_close 不齐，拒供：%s",
                           dict(list(rejected.items())[:8]))
        return store

    def _build(self, bars_by_symbol: dict[str, list],
               day_prev_close: dict[str, dict]) -> None:
        problems: list[str] = []
        for symbol, rows in bars_by_symbol.items():
            obid = to_order_book_id(symbol)
            series = []
            for r in rows:
                ts = str(r.timestamp if hasattr(r, "timestamp") else r["ts"])
                d = ts[:10]
                if self.today is not None and d > self.today.isoformat():
                    continue
                series.append((ts, r))
            series.sort(key=lambda x: x[0])
            if not series:
                continue
            arr = np.zeros(len(series), dtype=BAR_DTYPE_MINUTE)
            for col in ("prev_close", "limit_up", "limit_down", "total_turnover"):
                arr[col] = math.nan
            pcmap = day_prev_close.get(symbol, {})
            # 盘中/当日日线 raw 未出时，用最近可得日的 close 当"今日前收盘"
            # （band 语义 = 该分钟所属交易日的昨收；daily_bars_raw 只到昨天时，
            # 昨天的 close 就是今天的 prev_close）
            latest_known_close = None
            if pcmap:
                last_day = max(pcmap)
                latest_known_close = pcmap[last_day][1]

            def _g(row, key):
                """dict（db 返回）与对象两种行形态统一取值。"""
                if isinstance(row, dict):
                    return row.get(key)
                return getattr(row, key, None)

            for i, (ts, r) in enumerate(series):
                o, h, l, c = (float(_g(r, "open")), float(_g(r, "high")),
                              float(_g(r, "low")), float(_g(r, "close")))
                if not all(x > 0 and math.isfinite(x) for x in (o, h, l, c)):
                    problems.append(f"{symbol} {ts}：OHLC 非法")
                    continue
                arr[i]["datetime"] = _dt2i_minute(ts)
                arr[i]["open"], arr[i]["close"] = o, c
                arr[i]["high"], arr[i]["low"] = h, l
                arr[i]["volume"] = float(_g(r, "volume") or 0)
                amt = _g(r, "amount")
                arr[i]["total_turnover"] = float(amt) if amt else math.nan
                pcinfo = pcmap.get(ts[:10])
                if pcinfo:
                    pc = pcinfo[0]
                elif latest_known_close is not None:
                    pc = latest_known_close      # 当日日线未出：用最近已知收盘
                else:
                    pc = None
                if pc:
                    up, down = limit_prices(pc, symbol, is_st=False)
                    arr[i]["prev_close"] = pc
                    arr[i]["limit_up"], arr[i]["limit_down"] = up, down
            arr = arr[arr["datetime"] > 0]
            self._bars[obid] = arr
            self.stats[obid] = {
                "symbol": symbol, "bars": int(len(arr)),
                "bands_ok": int(np.isfinite(arr["limit_up"]).sum()) if len(arr) else 0,
            }
            if self.require_bands and len(arr):
                miss = int(np.count_nonzero(~np.isfinite(arr["limit_up"])))
                if miss == len(arr):
                    problems.append(f"{symbol} 全部分钟缺带（日线 raw prev_close 缺失？）")
                if not math.isfinite(arr["limit_up"][-1]):
                    problems.append(f"{symbol} 最后一根分钟缺带——拒供")
        if problems and self.require_bands:
            raise BandMissingError("M3b 分钟带门禁不过：" + "；".join(problems[:8]))

    # ---- 接口（与 RawDayBarStore 同签名）------------------------------
    def get_bars(self, order_book_id: str) -> np.ndarray:
        return self._bars.get(order_book_id, np.empty(0, dtype=BAR_DTYPE_MINUTE))

    def get_date_range(self, order_book_id: str):
        bars = self._bars.get(order_book_id)
        if bars is None or len(bars) == 0:
            t = self.today or date.today()
            v = t.year * 10_000_000_000 + t.month * 100_000_000 + t.day * 1_000_000
            return v, v
        return int(bars["datetime"][0]), int(bars["datetime"][-1])

    def order_book_ids(self) -> list:
        return sorted(self._bars)
