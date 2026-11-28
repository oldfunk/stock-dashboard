"""统一绩效（E3，2026-11-28）：纸盘 vs 基准 + AI 信号前向收益。

只读：纸盘 NAV（E0 桥）+ 母库 `benchmark_daily` / `analysis_outcome`。
纸盘按双方共有交易日对齐（不插值）；信号收益只统计已有前向窗口的样本。
"""
from __future__ import annotations

from typing import Optional

from src.paper_bridge import load_nav


def paper_series(rows: list[dict]) -> list[tuple[str, float]]:
    out = []
    for r in rows or []:
        ts = str(r.get("timestamp") or "")[:10]
        val = r.get("total_value")
        if ts and val is not None:
            out.append((ts, float(val)))
    return sorted(out, key=lambda x: x[0])


def bench_series(code: str) -> list[tuple[str, float]]:
    from src.models.database import db_conn
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT trade_date, close FROM benchmark_daily WHERE code = ? "
            "ORDER BY trade_date", (code,)).fetchall()
    return [(str(r["trade_date"])[:10], float(r["close"]))
            for r in rows if r["close"] is not None]


def aligned(paper: list[tuple[str, float]],
            bench: list[tuple[str, float]]) -> list[dict]:
    """按共有日期对齐，算逐日与累计收益/超额（首日无收益）。"""
    bmap = dict(bench)
    common = [(d, v, bmap[d]) for d, v in paper if d in bmap]
    if len(common) < 2:
        return []
    out = []
    p0, b0 = common[0][1], common[0][2]
    prev_p = prev_b = None
    for d, p, b in common:
        p_ret = (p / prev_p - 1.0) if prev_p else None
        b_ret = (b / prev_b - 1.0) if prev_b else None
        out.append({
            "date": d, "paper": p, "bench": b,
            "paper_ret": p_ret, "bench_ret": b_ret,
            "excess": (p_ret - b_ret) if (p_ret is not None
                                          and b_ret is not None) else None,
            "paper_cum": p / p0 - 1.0,
            "bench_cum": b / b0 - 1.0,
            "excess_cum": (p / p0 - 1.0) - (b / b0 - 1.0),
        })
        prev_p, prev_b = p, b
    return out


def paper_vs_benchmark(benchmark: str = "sh000300",
                       limit: int = 250) -> dict:
    nav = load_nav(limit=limit)
    if nav.get("source") == "unavailable":
        return {"available": False, "reason": nav.get("reason") or "NAV unavailable",
                "benchmark": benchmark, "rows": []}
    rows = aligned(paper_series(nav.get("data") or []), bench_series(benchmark))
    return {"available": bool(rows), "benchmark": benchmark, "rows": rows,
            "nav_asof": nav.get("asof")}


def signal_summary(rows: list[dict]) -> list[dict]:
    """按 signal 汇总前向收益/超额；NULL 样本不参与均值。"""
    buckets: dict[str, dict] = {}
    for r in rows or []:
        sig = str(r.get("signal") or "未知")
        b = buckets.setdefault(sig, {"signal": sig, "n": 0,
                                     "n_20": 0, "n_60": 0, "n_120": 0,
                                     "ret_20": [], "ret_60": [], "ret_120": [],
                                     "excess_20": [], "excess_60": [],
                                     "excess_120": []})
        b["n"] += 1
        for w in (20, 60, 120):
            v = r.get(f"ret_{w}")
            if v is not None:
                b[f"n_{w}"] += 1
                b[f"ret_{w}"].append(float(v))
            e = r.get(f"excess_{w}")
            if e is not None:
                b[f"excess_{w}"].append(float(e))
    out = []
    for sig in ("BUY", "HOLD", "AVOID"):
        b = buckets.get(sig)
        if not b:
            continue
        item = {"signal": sig, "n": b["n"]}
        for w in (20, 60, 120):
            rets, excess = b[f"ret_{w}"], b[f"excess_{w}"]
            item[f"n_{w}"] = b[f"n_{w}"]
            item[f"ret_{w}"] = (sum(rets) / len(rets)) if rets else None
            item[f"excess_{w}"] = (sum(excess) / len(excess)) if excess else None
        out.append(item)
    return out
