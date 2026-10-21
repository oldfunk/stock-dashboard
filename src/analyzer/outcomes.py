"""AI 分析信号的前向收益计算（2026-10-21）。

不做完整回测：只回答一个可验证的问题——每条已落库的 AI 判断，
在 T+1 收盘入场后，20/60/120 个交易日的真实收益是多少、相对基准如何。

口径（写死并在页面展示，避免“看起来专业但说不清”）：
- 入场：分析日之后第一个交易日收盘（分析在 15:30 后完成，T+1 可执行）。
- 收益：前复权收盘价，含现金分红再投资；基准为价格指数，不含分红。
- 未到期 / 缺行情：一律 None，绝不填 0。
- 完成后不重算：首次算出非空值即冻结，避免后续复权变化改写历史结论。
"""

import json
from typing import Iterable, Optional

HORIZONS = (20, 60, 120)

DEFAULT_BENCHMARKS = [
    {"code": "sh000300", "name": "沪深300"},
    {"code": "sh000922", "name": "中证红利"},
]

BENCHMARKS = DEFAULT_BENCHMARKS
BENCHMARK_NAMES = {b["code"]: b["name"] for b in DEFAULT_BENCHMARKS}

_SIGNAL_ORDER = {"BUY": 0, "HOLD": 1, "AVOID": 2}
_CONF_ORDER = {"高": 0, "中": 1, "低": 2}


def _loads(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return {}


def extract_signal(analysis_row: dict) -> tuple[str, str, str]:
    """从一条 stock_analysis_history 记录提取 (signal, confidence, verdict)。

    JSON 坏掉/老数据缺字段时返回空串，不猜测、不默认 BUY。
    """
    trade = _loads(analysis_row.get("ai_trade_strategy"))
    signal = str(trade.get("signal") or "").strip().upper()
    confidence = str(trade.get("confidence") or "").strip()
    parsed = _loads(analysis_row.get("ai_analysis"))
    verdict = str(parsed.get("verdict") or "").strip()
    return signal, confidence, verdict


def _clean_bars(bars: Iterable[dict]) -> list[dict]:
    out = []
    for b in bars or []:
        d = str(b.get("trade_date") or "")[:10]
        try:
            c = float(b.get("close"))
        except (TypeError, ValueError):
            continue
        if d and c > 0:
            out.append({"trade_date": d, "close": c})
    out.sort(key=lambda x: x["trade_date"])
    return out


def _pct(start: float, end: float) -> float:
    return round((end / start - 1.0) * 100.0, 4)


def compute_forward_outcome(analysis_date: str,
                            stock_bars: Iterable[dict],
                            bench_bars: Iterable[dict],
                            horizons: tuple[int, ...] = HORIZONS) -> dict:
    """计算一条分析的前向收益。

    返回字段：entry_date / entry_price / ret_{h} / bench_{h} / excess_{h}。
    任何一项缺数据都是 None；entry 找不到则全部 None。
    """
    result: dict = {"entry_date": None, "entry_price": None}
    for h in horizons:
        result[f"ret_{h}"] = None
        result[f"bench_{h}"] = None
        result[f"excess_{h}"] = None

    bars = _clean_bars(stock_bars)
    if not bars or not analysis_date:
        return result

    # 严格晚于分析日：分析日当天收盘不可用于入场（避免前视）
    entry_idx = next((i for i, b in enumerate(bars)
                      if b["trade_date"] > str(analysis_date)[:10]), None)
    if entry_idx is None:
        return result

    entry = bars[entry_idx]
    result["entry_date"] = entry["trade_date"]
    result["entry_price"] = entry["close"]

    bench = {b["trade_date"]: b["close"] for b in _clean_bars(bench_bars)}

    for h in horizons:
        exit_idx = entry_idx + h
        if exit_idx >= len(bars):
            continue
        exit_bar = bars[exit_idx]
        ret = _pct(entry["close"], exit_bar["close"])
        result[f"ret_{h}"] = ret

        b0 = bench.get(entry["trade_date"])
        b1 = bench.get(exit_bar["trade_date"])
        if b0 and b1:
            bench_ret = _pct(b0, b1)
            result[f"bench_{h}"] = bench_ret
            result[f"excess_{h}"] = round(ret - bench_ret, 4)

    return result


def is_hit(signal: str, excess: Optional[float]) -> Optional[bool]:
    """方向性命中：BUY 跑赢=命中，AVOID 跑输=命中，HOLD 不评命中率。"""
    if excess is None:
        return None
    s = str(signal or "").upper()
    if s == "BUY":
        return excess > 0
    if s == "AVOID":
        return excess < 0
    return None


def aggregate_outcomes(rows: Iterable[dict], horizon: int,
                       by_confidence: bool = False) -> list[dict]:
    """按 signal（可选再按 confidence）聚合已到期记录。

    只统计 excess 非空的记录；N 是分析条数，stocks 是去重股票数。
    重叠窗口会重复计数，页面必须同时展示 stocks 供判断。
    """
    groups: dict[tuple, dict] = {}
    for r in rows or []:
        excess = r.get(f"excess_{horizon}")
        if excess is None:
            continue
        key_signal = str(r.get("signal") or "未标注").upper()
        key_conf = str(r.get("confidence") or "未标注")
        key = (key_signal, key_conf if by_confidence else "全部")
        g = groups.setdefault(key, {
            "signal": key_signal,
            "confidence": key[1],
            "n": 0,
            "stocks": set(),
            "stock_sum": 0.0,
            "bench_sum": 0.0,
            "excess_sum": 0.0,
            "hit_n": 0,
            "hit_total": 0,
        })
        ret = r.get(f"ret_{horizon}")
        bench = r.get(f"bench_{horizon}")
        if ret is not None:
            g["stock_sum"] += float(ret)
        if bench is not None:
            g["bench_sum"] += float(bench)
        g["n"] += 1
        g["stocks"].add(r.get("stock_code"))
        g["excess_sum"] += float(excess)
        hit = is_hit(key_signal, float(excess))
        if hit is not None:
            g["hit_total"] += 1
            if hit:
                g["hit_n"] += 1

    out = []
    for g in groups.values():
        n = g["n"]
        if n == 0:
            continue
        out.append({
            "signal": g["signal"],
            "confidence": g["confidence"],
            "n": n,
            "stocks": len(g["stocks"]),
            "avg_stock": round(g["stock_sum"] / n, 2) if n else None,
            "avg_bench": round(g["bench_sum"] / n, 2) if n else None,
            "avg_excess": round(g["excess_sum"] / n, 2) if n else None,
            "hit_rate": (round(g["hit_n"] / g["hit_total"] * 100, 1)
                         if g["hit_total"] else None),
        })
    out.sort(key=lambda x: (_SIGNAL_ORDER.get(x["signal"], 9),
                            _CONF_ORDER.get(x["confidence"], 9),
                            x["signal"]))
    return out
