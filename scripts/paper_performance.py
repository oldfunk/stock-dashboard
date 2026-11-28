"""纸盘绩效表（B2，2026-11-24）：NAV vs 基准，逐日 + 累计超额。

只读：纸盘 nav_history（只读桥）+ 母库 benchmark_daily。
缺失日期按**双方共有的日期**对齐（不插值、不猜）；NAV 断档会如实少行。
核心计算在 `src/performance.py`（E3 复用）。

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

from src.performance import aligned, bench_series, paper_series  # noqa: E402
from src.paper_bridge import load_nav  # noqa: E402


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
