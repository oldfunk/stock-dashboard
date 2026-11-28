"""纸盘绩效表（B2，2026-11-24）：NAV vs 基准，逐日 + 累计超额。

只读：纸盘 nav_history（只读桥）+ 母库 benchmark_daily。
缺失日期按**双方共有的日期**对齐（不插值、不猜）；NAV 断档会如实少行。

用法：
    python scripts/paper_performance.py
    python scripts/paper_performance.py --benchmark sh000922 --limit 60 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.database import db_conn  # noqa: E402
from src.paper_bridge import load_nav  # noqa: E402


def paper_series(rows: list[dict]) -> list[tuple[str, float]]:
    out = []
    for r in rows or []:
        ts = str(r.get("timestamp") or "")[:10]
        val = r.get("total_value")
        if ts and val is not None:
            out.append((ts, float(val)))
    return sorted(out, key=lambda x: x[0])


def bench_series(code: str) -> list[tuple[str, float]]:
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
            "date": d,
            "paper": p,
            "bench": b,
            "paper_ret": p_ret,
            "bench_ret": b_ret,
            "excess": (p_ret - b_ret) if (p_ret is not None
                                          and b_ret is not None) else None,
            "paper_cum": p / p0 - 1.0,
            "bench_cum": b / b0 - 1.0,
            "excess_cum": (p / p0 - 1.0) - (b / b0 - 1.0),
        })
        prev_p, prev_b = p, b
    return out


def _pct(v) -> str:
    return "--" if v is None else f"{v * 100:+.2f}%"


def render(rows: list[dict], benchmark: str) -> str:
    if not rows:
        return "无可对齐的 NAV/基准日期（检查 nav_history 与 benchmark_daily）"
    lines = [
        f"# 纸盘绩效（基准 {benchmark}）",
        "",
        "| 日期 | 纸盘 NAV | 日收益 | 基准 | 日收益 | 日超额 | 累计纸盘 | 累计基准 | 累计超额 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['date']} | {r['paper']:.2f} | {_pct(r['paper_ret'])} | "
            f"{r['bench']:.2f} | {_pct(r['bench_ret'])} | {_pct(r['excess'])} | "
            f"{_pct(r['paper_cum'])} | {_pct(r['bench_cum'])} | "
            f"{_pct(r['excess_cum'])} |")
    first, last = rows[0], rows[-1]
    lines += ["", f"区间 {first['date']} → {last['date']}："
                  f"纸盘 {_pct(last['paper_cum'])} / 基准 {_pct(last['bench_cum'])}"
                  f" / 超额 **{_pct(last['excess_cum'])}**"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="纸盘 NAV vs 基准（只读）")
    ap.add_argument("--benchmark", default="sh000300")
    ap.add_argument("--limit", type=int, default=250)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    nav = load_nav(limit=args.limit)
    if nav.get("source") == "unavailable":
        print(f"纸盘 NAV 不可用：{nav.get('reason')}", file=sys.stderr)
        return 2
    paper = paper_series(nav.get("data") or [])
    bench = bench_series(args.benchmark)
    rows = aligned(paper, bench)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render(rows, args.benchmark))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
