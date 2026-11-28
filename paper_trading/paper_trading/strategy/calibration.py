"""P5.5 AI 成绩校准（离线/可重复，不调 LLM、不改交易）。

数据来源：母库 `analysis_outcome`（只读），verdict 为空时从
`stock_analysis_history.ai_analysis` 的 JSON 原文回填；同一 analysis 固定用
`sh000300` 一条基准，按 analysis_id 去重。

口径：
- 只用 `ret_20` 已到期的样本；`excess_20 = 个股20日收益 − 基准20日收益`；
- `BUY` 命中 = 超额 > 0；`AVOID` 命中 = 超额 < 0；`HOLD` 不作方向命中判断；
- 结论是**历史统计**，不是未来收益承诺，也不自动改 AI 风格；
- 样本不足（总数 <30 或 BUY/AVOID 各 <10）时 `sufficient=false`，
  只展示不据此下结论。

CLI:
    python -m paper_trading.cli strategy calibration --json
"""
from __future__ import annotations

import statistics
from typing import Optional

MIN_TOTAL = 30
MIN_PER_SIDE = 10
DEFAULT_BENCHMARK = "sh000300"


def verdict_class(verdict: str) -> str:
    s = str(verdict or "").strip()
    if s.startswith("通过"):
        return "pass"
    if s.startswith("不通过"):
        return "reject"
    if s.startswith("灰色"):
        return "gray"
    return "unknown"


def _num(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _hit(signal: str, excess: float) -> Optional[bool]:
    s = str(signal or "").upper()
    if s == "BUY":
        return excess > 0
    if s == "AVOID":
        return excess < 0
    return None


def _agg(items: list[dict]) -> dict:
    vals = [x["excess_20"] for x in items]
    hits = [x["hit"] for x in items if x["hit"] is not None]
    return {
        "n": len(items),
        "avg_excess_20": round(statistics.mean(vals), 6) if vals else None,
        "median_excess_20": round(statistics.median(vals), 6) if vals else None,
        "hit_rate": (round(sum(1 for h in hits if h) / len(hits), 4)
                     if hits else None),
        "avg_ret_20": (round(statistics.mean([x["ret_20"] for x in items]), 6)
                       if items else None),
    }


def build_calibration(*, benchmark: str = DEFAULT_BENCHMARK,
                      since: Optional[str] = None,
                      mother=None) -> dict:
    from paper_trading.integration import mother as m

    blk = m.outcome_samples(benchmark=benchmark, since=since, mother=mother)
    base = {
        "available": False, "sufficient": False, "benchmark": benchmark,
        "since": since, "n": 0, "window": None, "by_signal": {},
        "by_verdict": {}, "by_confidence": {}, "spread_buy_minus_avoid": None,
        "thresholds": {"min_total": MIN_TOTAL, "min_per_side": MIN_PER_SIDE},
        "source": blk.get("source"), "asof": blk.get("asof"),
        "note": "历史统计，不是未来收益承诺；不自动调整 AI 风格",
    }
    if not blk.get("available"):
        base["reason"] = blk.get("note") or "母库 analysis_outcome 不可用"
        return base
    rows = (blk.get("data") or {}).get("rows") or []
    usable = []
    for r in rows:
        verdict = str(r.get("verdict") or "").strip()
        signal = str(r.get("signal") or "").strip().upper()
        ret = _num(r.get("ret_20"))
        excess = _num(r.get("excess_20"))
        if ret is None or excess is None:
            continue
        # 旧样本（2026-07/08）母库没有 verdict 文本，但 signal + 前向收益齐全；
        # 成绩校准按 signal 统计，不能因为缺 verdict 把可用样本丢掉。
        if not verdict and not signal:
            continue
        usable.append({
            "analysis_id": r.get("analysis_id"),
            "symbol": r.get("stock_code"), "name": r.get("stock_name"),
            "date": str(r.get("analysis_date"))[:10],
            "signal": signal, "confidence": str(r.get("confidence") or ""),
            "verdict": verdict, "verdict_class": verdict_class(verdict),
            "ret_20": ret, "excess_20": excess,
            "hit": _hit(signal, excess),
        })
    if not usable:
        base["reason"] = ("有 verdict 的样本中暂无已到期的 ret_20"
                          "（新样本要等 20 个交易日）")
        base["asof"] = blk.get("asof")
        return base
    by_signal: dict[str, list] = {}
    by_verdict: dict[str, list] = {}
    by_conf: dict[str, list] = {}
    for x in usable:
        by_signal.setdefault(x["signal"] or "UNKNOWN", []).append(x)
        by_verdict.setdefault(x["verdict_class"], []).append(x)
        by_conf.setdefault(x["confidence"] or "未知", []).append(x)

    # verdict 的方向命中：通过=看多，不通过=看空/不买，灰色不判方向
    def _vhit(items):
        out = []
        for x in items:
            if x["verdict_class"] == "pass":
                out.append({**x, "hit": x["excess_20"] > 0})
            elif x["verdict_class"] == "reject":
                out.append({**x, "hit": x["excess_20"] < 0})
        return out

    out_signal = {k: _agg(v) for k, v in sorted(by_signal.items())}
    out_verdict = {k: _agg(_vhit(v)) for k, v in sorted(by_verdict.items())}
    out_conf = {k: _agg(v) for k, v in sorted(by_conf.items())}
    buy = out_signal.get("BUY", {})
    avoid = out_signal.get("AVOID", {})
    spread = None
    if buy.get("avg_excess_20") is not None and avoid.get("avg_excess_20") is not None:
        spread = round(buy["avg_excess_20"] - avoid["avg_excess_20"], 6)
    sufficient = (len(usable) >= MIN_TOTAL
                  and int(buy.get("n") or 0) >= MIN_PER_SIDE
                  and int(avoid.get("n") or 0) >= MIN_PER_SIDE)
    warnings = []
    if int(buy.get("n") or 0) < 30:
        warnings.append(f"BUY 样本仅 {buy.get('n') or 0} 条，spread 易受个别样本影响")
    if int(avoid.get("n") or 0) < 30:
        warnings.append(f"AVOID 样本仅 {avoid.get('n') or 0} 条，结论不稳")
    dates = [x["date"] for x in usable if x["date"]]
    base.update({
        "available": True, "sufficient": sufficient, "n": len(usable),
        "window": {"start": min(dates) if dates else None,
                   "end": max(dates) if dates else None},
        "by_signal": out_signal, "by_verdict": out_verdict,
        "by_confidence": out_conf, "spread_buy_minus_avoid": spread,
        "recovered_verdicts": (blk.get("data") or {}).get("recovered"),
        "duplicates_dropped": ((blk.get("data") or {}).get("raw_count", 0)
                               - (blk.get("data") or {}).get("unique_count", 0)),
        "asof": blk.get("asof"),
        "warnings": warnings,
        "units": "excess_20/ret_20 为百分点（10.39 = +10.39%）；hit_rate 为 0-1 小数",
        "note": ("历史统计，不是未来收益承诺；BUY 命中=20日超额>0，"
                 "AVOID 命中=20日超额<0；旧样本无 verdict 时按 signal 统计；"
                 "样本不足时不据此下结论"),
    })
    if not sufficient:
        base["warning"] = (f"样本不足：总 {len(usable)}（需 ≥{MIN_TOTAL}），"
                           f"BUY {buy.get('n') or 0} / AVOID {avoid.get('n') or 0}"
                           f"（各需 ≥{MIN_PER_SIDE}）；只展示，不做结论")
    return base
