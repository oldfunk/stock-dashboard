"""H-001 的 regime 混杂检查（只读，2026-11-04，批15 15-e）。

母方首跑：≥40 档 T+60 超额 17.05pp、verdict=supported，但 22 行全在 risk_off，
怀疑"这不是 MOS 的预测力，而是 risk_off 环境的均值回归暴露"。本脚本把三件事算清楚：

1. **成熟度 × regime 的共线检查**：某个 horizon 的成熟样本里，非某 regime 的行数
   有多少？如果 T+60 只 in risk_off 期成熟过，那么"MOS 有效"与"risk_off 期反弹"
   在数据上**根本不可分离**——再多分档都救不回来，只能等样本长大。
2. **同 regime 内的档位差**：只在 risk_off 里，各档相对 `<=0` 档还差多少（按股票
   分块 bootstrap 出差值 CI）。环境基线扣掉之后剩下的才是 MOS 的增量。
3. **档位构成**：每档几只票、哪些日期、与基线档的股票有没有重叠——
   两档股票零重叠时，档间差同时也在比"不同的公司"。

只读：`mode=ro` URI + `PRAGMA query_only=ON`；不写任何表。

    python scripts/h001_regime_check.py                      # 默认两个 horizon
    python scripts/h001_regime_check.py --benchmark sh000922 --format md
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

TIERS: tuple[tuple[str, float | None, float | None], ...] = (
    ("<=0", None, 0.0), ("0~20", 0.0, 20.0),
    ("20~40", 20.0, 40.0), (">=40", 40.0, None),
)
HORIZONS = ("excess_20", "excess_60")


def tier_of(mos):
    """MOS → 档位名（与 experiment._tier_metrics 的默认档一致）。None/负无穷安全。"""
    if mos is None:
        return None
    for name, lo, hi in TIERS:
        if (lo is None or mos >= lo) and (hi is None or mos < hi):
            return name
    return None


def open_read_only(db_path: str) -> sqlite3.Connection:
    """只读连接：URI mode=ro + query_only，物理上写不进去。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def load_rows(conn: sqlite3.Connection, benchmark: str) -> list[dict]:
    """分析结论行 + 当日 regime（LEFT JOIN：无 regime 标签的行保留为 None）。"""
    rows = conn.execute(
        "SELECT o.stock_code, o.analysis_date, o.mos_at_entry,"
        "       o.excess_20, o.excess_60, m.regime "
        "FROM analysis_outcome o "
        "LEFT JOIN market_state_daily m ON m.trade_date = o.analysis_date "
        "WHERE o.benchmark_code = ? ORDER BY o.analysis_date",
        (benchmark,)).fetchall()
    return [dict(r) for r in rows]


UNLABELED = "无标签"


def maturity_by_regime(rows: list[dict], col: str) -> dict:
    """该 horizon 的成熟样本按 regime 计数（共线检查用）。"""
    out: dict[str, int] = {}
    for r in rows:
        if r.get(col) is None:
            continue
        key = r.get("regime") or UNLABELED
        out[key] = out.get(key, 0) + 1
    return out


def tier_regime_means(rows: list[dict], col: str) -> list[dict]:
    """(regime, 档) 格子的行数 / 股票数 / 均值（pp）。"""
    grid: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get(col) is None or r.get("regime") is None:
            continue
        t = tier_of(r.get("mos_at_entry"))
        if t:
            grid.setdefault((r["regime"], t), []).append(r)
    out = []
    for (regime, tier), rs in sorted(grid.items()):
        out.append({
            "regime": regime, "tier": tier, "rows": len(rs),
            "stocks": len({x["stock_code"] for x in rs}),
            "mean_pp": round(statistics.mean(x[col] for x in rs), 3),
        })
    return out


def _by_stock(rows: list[dict], col: str) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = {}
    for r in rows:
        if r.get(col) is not None:
            groups.setdefault(r["stock_code"], []).append(r[col])
    return groups


def bootstrap_diff_ci(cur_rows, base_rows, col: str, n_boot: int = 2000,
                      seed: int = 42) -> tuple[float, float, float]:
    """`cur` 相对 `base` 的均值差 + 按股票分块的 95% CI（单位同原列，pp）。

    分块单位是**股票**而非行：一只票在窗口里常有十几行（同一分析反复到期），
    按行自助会把有效样本夸大十几倍。两组各自独立重抽样后取差，是常见的保守近似。
    """
    cur, base = _by_stock(cur_rows, col), _by_stock(base_rows, col)
    if not cur or not base:
        return (float("nan"), float("nan"), float("nan"))
    diff = (statistics.mean([v for vs in cur.values() for v in vs])
            - statistics.mean([v for vs in base.values() for v in vs]))
    rng = random.Random(seed)
    ck, bk = list(cur), list(base)
    dist = []
    for _ in range(n_boot):
        c = [rng.choice(cur[k]) for k in ck for _ in cur[k]]
        b = [rng.choice(base[k]) for k in bk for _ in base[k]]
        dist.append(statistics.mean(c) - statistics.mean(b))
    dist.sort()
    return (diff, dist[int(0.025 * n_boot)], dist[int(0.975 * n_boot)])


def within_regime_diffs(rows: list[dict], col: str, regime: str,
                        n_boot: int, seed: int) -> list[dict]:
    """同一 regime 内：各档相对 `<=0` 基线档的差与 CI（扣掉环境基线才谈 MOS）。"""
    pool = [r for r in rows if r.get("regime") == regime and r.get(col) is not None]
    base = [r for r in pool if tier_of(r.get("mos_at_entry")) == "<=0"]
    out = []
    for name, _lo, _hi in TIERS[1:]:
        cur = [r for r in pool if tier_of(r.get("mos_at_entry")) == name]
        rec = {"regime": regime, "tier": name, "rows": len(cur),
               "base_rows": len(base),
               "stocks": len({x["stock_code"] for x in cur})}
        if len(cur) < 3 or len(base) < 3:
            rec["verdict"] = "样本不足"
            out.append(rec)
            continue
        d, lo, hi = bootstrap_diff_ci(cur, base, col, n_boot=n_boot, seed=seed)
        rec.update({"diff_pp": round(d, 3), "ci_low_pp": round(lo, 3),
                    "ci_high_pp": round(hi, 3),
                    "verdict": ("CI 不含 0" if lo > 0 or hi < 0 else "CI 跨 0")})
        out.append(rec)
    return out


def tier_composition(rows: list[dict], tier: str) -> dict:
    """某一档的行/票/日期构成 + 与 `<=0` 档的股票重叠（重叠=0 时档间差混着选股差）。"""
    sel = [r for r in rows if tier_of(r.get("mos_at_entry")) == tier]
    base_stocks = {r["stock_code"] for r in rows
                   if tier_of(r.get("mos_at_entry")) == "<=0"}
    stocks = {r["stock_code"] for r in sel}
    return {
        "tier": tier, "rows": len(sel), "stocks": len(stocks),
        "dates": len({r["analysis_date"] for r in sel}),
        "regime_counts": {k or "无标签": sum(1 for r in sel if r.get("regime") == k)
                          for k in {r.get("regime") for r in sel}},
        "overlap_with_base": sorted(stocks & base_stocks),
    }


def analyze(rows: list[dict], n_boot: int = 2000, seed: int = 42) -> dict:
    out: dict = {"benchmark_rows": len(rows),
                 "stocks": len({r["stock_code"] for r in rows}),
                 "regime_missing_rows": sum(1 for r in rows if r["regime"] is None),
                 "horizons": {}}
    for col in HORIZONS:
        mature = maturity_by_regime(rows, col)
        # 对照组只能是**有 regime 标签**的非 risk_off 行：无标签行既不算 risk_off
        # 也不能当对照，拿它把"共线"判成"不共线"是自欺（产线现况就是 1 行无标签）。
        outside = sum(v for k, v in mature.items()
                      if k not in ("risk_off", UNLABELED))
        out["horizons"][col] = {
            "mature_by_regime": mature,
            "mature_rows_outside_risk_off": outside,
            "mature_rows_unlabeled": mature.get(UNLABELED, 0),
            "confounded": outside == 0,
            "tier_means": tier_regime_means(rows, col),
            "within_risk_off_vs_base": within_regime_diffs(
                rows, col, "risk_off", n_boot, seed),
            "tier_ge40_composition": tier_composition(rows, ">=40"),
        }
    return out


def to_markdown(res: dict, benchmark: str) -> str:
    lines = [f"# H-001 regime 混杂检查（benchmark={benchmark}）", "",
             f"分析行 {res['benchmark_rows']} / 股票 {res['stocks']} 只 /"
             f" regime 缺失 {res['regime_missing_rows']} 行", ""]
    for col, h in res["horizons"].items():
        lines += [f"## {col}", "",
                  f"- 成熟样本 regime 分布：`{h['mature_by_regime']}`",
                  f"- 可作对照的非 risk_off 成熟行：**{h['mature_rows_outside_risk_off']}**"
                  f"（另有 {h['mature_rows_unlabeled']} 行无 regime 标签，不能当对照）"
                  + (" → 与 regime **完全共线**，档位差无法归因"
                     if h["confounded"] else ""),
                  "",
                  "| regime | 档 | 行 | 只 | 均值 pp |", "|---|---|---:|---:|---:|"]
        lines += [f"| {t['regime']} | {t['tier']} | {t['rows']} | {t['stocks']}"
                  f" | {t['mean_pp']} |" for t in h["tier_means"]]
        lines += ["", "**risk_off 内各档相对 <=0 档**", ""]
        for w in h["within_risk_off_vs_base"]:
            if "diff_pp" not in w:
                lines.append(f"- {w['tier']}：{w['verdict']}"
                             f"（{w['rows']} 行 vs 基线 {w['base_rows']} 行）")
            else:
                lines.append(
                    f"- {w['tier']}：{w['rows']} 行/{w['stocks']} 只，差"
                    f" **{w['diff_pp']:+.2f}pp**"
                    f" [{w['ci_low_pp']:+.2f}, {w['ci_high_pp']:+.2f}] → {w['verdict']}")
        c = h["tier_ge40_composition"]
        lines += ["", f">=40 档构成：{c['rows']} 行 / {c['stocks']} 只 /"
                      f" {c['dates']} 个交易日，regime `{c['regime_counts']}`，"
                      f"与基线档股票重叠 {len(c['overlap_with_base'])} 只", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H-001 的 regime 混杂检查（只读）")
    ap.add_argument("--db", default=str(PROJ / "data/db/stock_dashboard.db"))
    ap.add_argument("--benchmark", default="sh000300")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--format", choices=("json", "md"), default="json")
    args = ap.parse_args(argv)

    conn = open_read_only(args.db)
    try:
        rows = load_rows(conn, args.benchmark)
    finally:
        conn.close()
    if not rows:
        print(f"benchmark={args.benchmark} 无分析结论行", file=sys.stderr)
        return 1
    res = analyze(rows, n_boot=args.n_boot, seed=args.seed)
    print(to_markdown(res, args.benchmark) if args.format == "md"
          else json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
