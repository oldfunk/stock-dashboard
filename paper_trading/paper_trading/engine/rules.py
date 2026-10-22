"""规则引擎内核：纯函数，盘中守护与回测共用同一套判定逻辑。

设计原则（与项目 fail-closed 一致）：
- 规则只产出「意图」，绝不直接下单；执行必须过 RiskManager + 撮合
- 盘中价规则只用实时价；日线规则（T 日收盘算出的信号）最早 T+1 开盘执行，
  回测与实盘共用该纪律，杜绝未来函数
- 日内规则只用**当日**分钟线（截止当前时点），天然无未来函数
- 规则集来自 YAML，用户可改；引擎本身不含任何"隐藏策略"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

RULE_TYPES = ("ma_cross", "rsi_zone", "price_level", "pct_change",
              "trailing_stop", "time_stop",
              # 日内规则（依赖分钟线；无分钟数据时自动静默不触发）
              "vwap_dev", "intraday_pullback", "minute_ma_cross")

#: 需要当日分钟线的规则类型（面板/文档据此提示"先 minute pull"）
INTRADAY_TYPES = ("vwap_dev", "intraday_pullback", "minute_ma_cross")


@dataclass
class PositionView:
    """规则看到的持仓视图（盘中实时维护）。"""
    symbol: str
    total_volume: int = 0
    available_volume: int = 0
    avg_cost: float = 0.0
    entry_day: str = ""          # 建仓日 YYYY-MM-DD
    peak_price: float = 0.0      # 持仓期间最高价（移动止盈用）


@dataclass
class RuleContext:
    """一次判定的全部输入。"""
    now: str                      # ISO 时间
    day: str                      # YYYY-MM-DD
    symbol: str
    price: float = 0.0            # 盘中实时价（无则用当日收盘）
    prev_close: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    change_pct: float = 0.0       # 当日涨跌幅 %
    closes: list[float] = field(default_factory=list)   # 日线收盘（含今日，按序）
    position: Optional[PositionView] = None
    trading_days_held: int = 0    # 已持有的交易日数
    minutes: list[tuple] = field(default_factory=list)  # 当日分钟线，按序
    #   每项 (ts, open, high, low, close, volume, amount)，ts 为 ISO 字符串


@dataclass
class Intent:
    """规则命中产出：想做什么（还没过风控）。"""
    rule_id: str
    symbol: str
    side: str                     # buy | sell
    reason: str
    ref_price: float = 0.0
    all_out: bool = False


def _sma(xs: list[float], n: int) -> Optional[float]:
    if n <= 0 or len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def rsi(closes: list[float], n: int = 14) -> Optional[float]:
    """Wilder 简化版 RSI（均值口径）。数据不足返回 None。"""
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[-(n + 1):-1], closes[-n:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains) / n, sum(losses) / n
    if al <= 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


# ---------- 各规则判定（纯函数；返回 Intent 或 None） ----------

def _ma_cross(ctx: RuleContext, p: dict) -> Optional[Intent]:
    short = int(p.get("short", 5))
    long = int(p.get("long", 20))
    xs = ctx.closes
    if len(xs) < long + 1:
        return None
    prev_s, prev_l = _sma(xs[:-1], short), _sma(xs[:-1], long)
    cur_s, cur_l = _sma(xs, short), _sma(xs, long)
    if None in (prev_s, prev_l, cur_s, cur_l):
        return None
    up = prev_s <= prev_l and cur_s > cur_l
    dn = prev_s >= prev_l and cur_s < cur_l
    if not up and not dn:
        return None
    return Intent("", ctx.symbol, "buy" if up else "sell",
                  f"MA{short}{'上穿' if up else '下穿'}MA{long}", ctx.price)


def _rsi_zone(ctx: RuleContext, p: dict) -> Optional[Intent]:
    n = int(p.get("n", 14))
    lower = float(p.get("lower", 30))
    upper = float(p.get("upper", 70))
    xs = ctx.closes
    if len(xs) < n + 2:
        return None
    cur = rsi(xs, n)
    prev = rsi(xs[:-1], n)
    if cur is None or prev is None:
        return None
    if prev > lower and cur <= lower:
        return Intent("", ctx.symbol, "buy", f"RSI{n} 下穿 {lower:g}（超卖）", ctx.price)
    if prev < upper and cur >= upper:
        return Intent("", ctx.symbol, "sell", f"RSI{n} 上穿 {upper:g}（超买）", ctx.price)
    return None


def _price_level(ctx: RuleContext, p: dict) -> Optional[Intent]:
    op = str(p.get("op", "below")).lower()
    val = float(p.get("value", 0) or 0)
    if val <= 0 or ctx.price <= 0:
        return None
    if op == "below" and ctx.price <= val:
        return Intent("", ctx.symbol, "buy", f"现价 {ctx.price:.2f} ≤ {val:.2f}", ctx.price)
    if op == "above" and ctx.price >= val:
        return Intent("", ctx.symbol, "sell", f"现价 {ctx.price:.2f} ≥ {val:.2f}", ctx.price)
    return None


def _pct_change(ctx: RuleContext, p: dict) -> Optional[Intent]:
    thr = float(p.get("pct", 0) or 0)
    if thr <= 0:
        return None
    base = ctx.prev_close or 0.0
    pct = ctx.change_pct if ctx.prev_close else ((ctx.price - base) / base * 100.0
                                                 if base else 0.0)
    if pct <= -abs(thr):
        return Intent("", ctx.symbol, "buy", f"日内跌幅 {pct:.2f}% ≤ -{abs(thr):g}%", ctx.price)
    if pct >= abs(thr):
        return Intent("", ctx.symbol, "sell", f"日内涨幅 {pct:.2f}% ≥ {abs(thr):g}%", ctx.price)
    return None


def _trailing_stop(ctx: RuleContext, p: dict) -> Optional[Intent]:
    pct = float(p.get("pct", 0) or 0)
    pos = ctx.position
    if pct <= 0 or not pos or pos.total_volume <= 0 or ctx.price <= 0:
        return None
    peak = max(pos.peak_price or 0.0, pos.avg_cost, ctx.day_high or 0.0)
    if peak <= 0:
        return None
    drop = (peak - ctx.price) / peak * 100.0
    if drop >= pct:
        return Intent("", ctx.symbol, "sell",
                      f"自高点 {peak:.2f} 回落 {drop:.2f}% ≥ {pct:g}%（移动止盈）",
                      ctx.price, all_out=True)
    return None


def _time_stop(ctx: RuleContext, p: dict) -> Optional[Intent]:
    days = int(p.get("days", 3) or 0)
    pos = ctx.position
    if days <= 0 or not pos or pos.total_volume <= 0:
        return None
    if ctx.trading_days_held >= days:
        return Intent("", ctx.symbol, "sell",
                      f"持有 {ctx.trading_days_held} 个交易日 ≥ {days}（时间止损）",
                      ctx.price, all_out=True)
    return None


# ---------- 日内规则（只用当日分钟线，截至当前时点；无分钟数据 → 不触发） ----------

def vwap(minutes: list[tuple]) -> Optional[float]:
    """当日成交量加权均价；无成交额时退化为成交量加权，拿不到量则返回 None。"""
    num = den = 0.0
    for m in minutes:
        try:
            px = float(m[4])          # close
            amt = float(m[6] or 0)     # amount
            vol = float(m[5] or 0)     # volume
        except (TypeError, ValueError, IndexError):
            continue
        if amt <= 0 and vol <= 0:
            continue
        w = amt if amt > 0 else px * vol
        num += px * w
        den += w
    return (num / den) if den > 0 else None


def _vwap_dev(ctx: RuleContext, p: dict) -> Optional[Intent]:
    """现价偏离当日 VWAP 达阈值：低于 → 买（均值回归），高于 → 卖。

    只用截至当前分钟的数据，不含未来；开盘头几根 VWAP 不稳，min_bars 兜底。
    注意 min_bars 数的是**库里存的 K 线根数**，不是分钟数——现在存的是
    5 分钟 K 线（48 根/日），min_bars=5 约等于开盘后 25 分钟。
    """
    pct = abs(float(p.get("pct", 0) or 0))
    min_bars = int(p.get("min_bars", 5) or 0)
    if pct <= 0 or ctx.price <= 0 or len(ctx.minutes) < max(1, min_bars):
        return None
    vw = vwap(ctx.minutes)
    if not vw or vw <= 0:
        return None
    dev = (ctx.price - vw) / vw * 100.0
    if dev <= -pct:
        return Intent("", ctx.symbol, "buy",
                      f"现价 {ctx.price:.2f} 低于当日均价 {vw:.2f} {abs(dev):.2f}%（超跌回归）",
                      ctx.price)
    if dev >= pct:
        return Intent("", ctx.symbol, "sell",
                      f"现价 {ctx.price:.2f} 高于当日均价 {vw:.2f} {dev:.2f}%（溢价兑现）",
                      ctx.price)
    return None


def _intraday_pullback(ctx: RuleContext, p: dict) -> Optional[Intent]:
    """日内冲高回落：先涨 gain_pct% 以上，再自当日高点回落 drop_pct% → 卖出。

    gain_pct 缺省 0（不要求先涨）；回落判定用当日分钟线最高价，
    与 trailing_stop（按持仓期高点）的区别在这里是**当日**。
    """
    drop = abs(float(p.get("drop_pct", 0) or 0))
    gain = float(p.get("gain_pct", 0) or 0)
    if drop <= 0 or ctx.price <= 0:
        return None
    mhi = mlo = 0.0
    for m in ctx.minutes:
        try:
            mhi = max(mhi, float(m[2]))
            mlo = min(mlo or float(m[3]), float(m[3]))
        except (TypeError, ValueError, IndexError):
            continue
    hi = max(mhi, ctx.day_high or 0.0)
    base = ctx.prev_close or 0.0
    if gain > 0:
        if not base or (ctx.price - base) / base * 100.0 < gain:
            return None
    if hi <= 0 or ctx.price >= hi:
        return None
    fall = (hi - ctx.price) / hi * 100.0
    if fall >= drop:
        return Intent("", ctx.symbol, "sell",
                      f"自日内高点 {hi:.2f} 回落 {fall:.2f}% ≥ {drop:g}%（冲高回落）",
                      ctx.price, all_out=bool(p.get("all_out", True)))
    return None


def _minute_ma_cross(ctx: RuleContext, p: dict) -> Optional[Intent]:
    """分钟线均线交叉：短均线上穿长均线 → 买，下穿 → 卖。"""
    short = int(p.get("short", 5) or 5)
    long = int(p.get("long", 20) or 20)
    closes = [float(m[4]) for m in ctx.minutes
              if isinstance(m, (list, tuple)) and len(m) > 4 and m[4] is not None]
    if len(closes) < long + 1:
        return None
    prev_s, prev_l = _sma(closes[:-1], short), _sma(closes[:-1], long)
    cur_s, cur_l = _sma(closes, short), _sma(closes, long)
    if None in (prev_s, prev_l, cur_s, cur_l):
        return None
    up = prev_s <= prev_l and cur_s > cur_l
    dn = prev_s >= prev_l and cur_s < cur_l
    if not up and not dn:
        return None
    px = ctx.price or cur_s
    return Intent("", ctx.symbol, "buy" if up else "sell",
                  f"分钟 MA{short}{'上穿' if up else '下穿'}MA{long}"
                  f"（{cur_s:.2f} / {cur_l:.2f}）", px)


_EVAL: dict[str, Callable[[RuleContext, dict], Optional[Intent]]] = {
    "ma_cross": _ma_cross,
    "rsi_zone": _rsi_zone,
    "price_level": _price_level,
    "pct_change": _pct_change,
    "trailing_stop": _trailing_stop,
    "time_stop": _time_stop,
    "vwap_dev": _vwap_dev,
    "intraday_pullback": _intraday_pullback,
    "minute_ma_cross": _minute_ma_cross,
}


# 方向由信号自身决定的规则类型：ma_cross / rsi_zone / pct_change 等
# （这些类型的语义就是"金叉买/死叉卖"，action.side 只作默认，不覆盖信号方向；
#   想反着来就拆成两条规则）
_DIRECTIONAL = ("ma_cross", "rsi_zone", "pct_change",
                "vwap_dev", "intraday_pullback", "minute_ma_cross")


def evaluate(rule: dict, ctx: RuleContext) -> Optional[Intent]:
    """按规则判定一次；类型未知/参数缺失一律 None（fail-closed）。"""
    rtype = str(rule.get("type", ""))
    fn = _EVAL.get(rtype)
    if fn is None:
        return None
    if not rule.get("symbol") and not rule.get("symbols"):
        return None
    try:
        it = fn(ctx, dict(rule.get("params") or {}))
    except Exception:      # noqa: BLE001 - 规则异常不得拖垮引擎
        return None
    if it is None:
        return None
    it.rule_id = str(rule.get("id") or "")
    if rtype not in _DIRECTIONAL:
        side = str((rule.get("action") or {}).get("side", it.side)).lower()
        if side in ("buy", "sell"):
            it.side = side
    if (rule.get("action") or {}).get("all"):
        it.all_out = True
    return it


def size_for_value(price: float, value: float, lot: int = 100) -> int:
    """按金额算股数（向下取整到整手；买不起一手返回 0）。"""
    if price <= 0 or value <= 0 or lot <= 0:
        return 0
    lots = int(value / price) // lot
    return lots * lot