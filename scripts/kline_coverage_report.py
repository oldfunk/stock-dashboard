"""K 线当日覆盖报告（A3，2026-11-28，只读）。

回答三个问题：
1. 全市场当日 `kline_daily` 覆盖多少（轮换池只拉一部分，正常远低于 100%）；
2. `daily_close` 覆盖是否全（全市场收盘覆盖层，正常 100%）；
3. 持仓 + 最新候选池是否都有当日 K 线（T96 的硬要求，A2 的目标 100%）。

只读母库，不写任何表；缺失如实列出，不插值，不猜原因。
用法：
    python scripts/kline_coverage_report.py
    python scripts/kline_coverage_report.py --date 2026-11-27 --days 5 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.database import db_conn  # noqa: E402
from src.paper_bridge import load_positions  # noqa: E402


def board(code: str) -> str:
    """按代码前缀粗分板块（只用于聚合展示，不做交易判断）。"""
    c = str(code).zfill(6)
    if c.startswith(("688", "689")):
        return "科创板"
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith(("920", "8", "4")):
        return "北交所"
    if c.startswith(("600", "601", "603", "605")):
        return "沪主板"
    if c.startswith(("000", "001", "002", "003")):
        return "深主板"
    return "其他"


def coverage(active_codes: list[str], kline_codes: set[str],
             daily_codes: set[str]) -> dict:
    """全市场覆盖率 + 缺失按板块聚合（纯函数）。"""
    active = {str(c).zfill(6) for c in active_codes}
    kline = {str(c).zfill(6) for c in kline_codes} & active
    daily = {str(c).zfill(6) for c in daily_codes} & active
    missing = active - kline
    by_board: dict[str, int] = {}
    for code in missing:
        b = board(code)
        by_board[b] = by_board.get(b, 0) + 1
    total = len(active)
    return {
        "active": total,
        "kline": len(kline),
        "kline_pct": (len(kline) / total) if total else 0.0,
        "daily_close": len(daily),
        "daily_close_pct": (len(daily) / total) if total else 0.0,
        "missing": len(missing),
        "missing_by_board": dict(sorted(by_board.items(),
                                        key=lambda kv: -kv[1])),
        "missing_sample": sorted(missing),
    }


def target_status(targets: list[dict], last_dates: dict[str, str],
                  date: str) -> list[dict]:
    """持仓 + 候选池逐只新鲜度；last_dates 缺键视为从未有 K 线。"""
    out = []
    for t in targets:
        code = str(t["code"]).zfill(6)
        last = last_dates.get(code)
        out.append({
            "code": code,
            "name": t.get("name") or "",
            "roles": t.get("roles") or [],
            "last_date": last,
            "fresh": last == date,
        })
    return out


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def render(report: dict) -> str:
    cov = report["coverage"]
    targets = report["targets"]
    fresh = sum(1 for t in targets if t["fresh"])
    total = len(targets)
    lines = [
        f"# K 线当日覆盖（{report['date']}）",
        "",
        f"- 活跃 A 股：{cov['active']}",
        f"- 全市场 `kline_daily`：{cov['kline']} 只（{_pct(cov['kline_pct'])}）",
        f"- `daily_close`：{cov['daily_close']} 只（{_pct(cov['daily_close_pct'])}）",
        f"- 持仓 + 候选池：**{fresh}/{total}** 当日"
        f"（{_pct(fresh / total) if total else '--'}）",
        "",
    ]
    if report.get("trend"):
        lines += ["## 近几日全市场覆盖", "",
                  "| 交易日 | kline_daily | daily_close |", "|---|---:|---:|"]
        for r in report["trend"]:
            lines.append(f"| {r['date']} | {r['kline']} | "
                         f"{r['daily_close']} |")
        lines.append("")
    if targets:
        lines += ["## 目标池", "",
                  "| 代码 | 名称 | 角色 | 末根 | 当日 |",
                  "|---|---|---|---|---|"]
        for t in targets:
            lines.append(
                f"| {t['code']} | {t['name']} | {'/'.join(t['roles'])} | "
                f"{t['last_date'] or '--'} | {'✓' if t['fresh'] else '缺'} |")
        lines.append("")
    if cov["missing"]:
        lines += ["## 全市场 K 线缺失", "",
                  f"共 {cov['missing']} 只：", ""]
        for b, n in cov["missing_by_board"].items():
            lines.append(f"- {b}：{n}")
        sample = cov["missing_sample"][:report.get("missing_limit", 20)]
        if sample:
            lines += ["", "样例：" + "、".join(sample)]
        lines.append("")
    return "\n".join(lines)


def _latest_run(conn) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM screening_result GROUP BY run_id "
        "ORDER BY MAX(run_date) DESC, run_id DESC LIMIT 1").fetchone()
    return row["run_id"] if row else None


def _query_targets(conn, pool_limit: int) -> list[dict]:
    """持仓 + 最新筛选 TopN，按代码合并角色。"""
    merged: dict[str, dict] = {}
    pos = load_positions()
    for p in (pos.get("data") or []):
        if int(p.get("total_volume") or 0) <= 0:
            continue
        code = str(p.get("symbol")).zfill(6)
        merged.setdefault(code, {"code": code, "roles": []})["roles"].append("持仓")
    run_id = _latest_run(conn)
    if run_id:
        for r in conn.execute(
                "SELECT code, name FROM screening_result WHERE run_id = ? "
                "ORDER BY score DESC LIMIT ?", (run_id, int(pool_limit))):
            code = str(r["code"]).zfill(6)
            item = merged.setdefault(code, {"code": code, "name": r["name"],
                                            "roles": []})
            item["name"] = item.get("name") or r["name"]
            if "候选" not in item["roles"]:
                item["roles"].append("候选")
    # 持仓名称补全
    codes = list(merged)
    if codes:
        q = ("SELECT code, name FROM stock_snapshot WHERE code IN (%s)"
             % ",".join("?" * len(codes)))
        for r in conn.execute(q, codes):
            item = merged[str(r["code"]).zfill(6)]
            item["name"] = item.get("name") or r["name"]
    return [merged[c] for c in sorted(merged)]


def collect(conn, date: str | None = None, days: int = 5,
            pool_limit: int = 20) -> dict:
    active = [str(r["code"]).zfill(6) for r in conn.execute(
        "SELECT code FROM stock_snapshot WHERE market = 'A' ORDER BY code")]
    if not date:
        row = conn.execute("SELECT MAX(trade_date) d FROM kline_daily").fetchone()
        date = row["d"] if row else None
    if not date:
        raise SystemExit("kline_daily 为空，无法确定交易日")
    kline_codes = {str(r["code"]).zfill(6) for r in conn.execute(
        "SELECT DISTINCT code FROM kline_daily WHERE trade_date = ?", (date,))}
    daily_codes = {str(r["code"]).zfill(6) for r in conn.execute(
        "SELECT DISTINCT code FROM daily_close WHERE trade_date = ?", (date,))}
    targets = _query_targets(conn, pool_limit)
    last: dict[str, str] = {}
    if targets:
        codes = [t["code"] for t in targets]
        q = ("SELECT code, MAX(trade_date) d FROM kline_daily WHERE code IN (%s) "
             "GROUP BY code" % ",".join("?" * len(codes)))
        last = {str(r["code"]).zfill(6): r["d"]
                for r in conn.execute(q, codes)}
    k_by_date = {r["d"]: int(r["n"]) for r in conn.execute(
        "SELECT trade_date d, COUNT(DISTINCT code) n FROM kline_daily "
        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?", (int(days),))}
    d_by_date = {r["d"]: int(r["n"]) for r in conn.execute(
        "SELECT trade_date d, COUNT(DISTINCT code) n FROM daily_close "
        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?", (int(days),))}
    trend = [{"date": d, "kline": k_by_date.get(d, 0),
              "daily_close": d_by_date.get(d, 0)}
             for d in sorted(set(k_by_date) | set(d_by_date))]
    return {
        "date": date,
        "coverage": coverage(active, kline_codes, daily_codes),
        "targets": target_status(targets, last, date),
        "trend": trend,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="K 线当日覆盖报告（只读）")
    ap.add_argument("--date", help="交易日 YYYY-MM-DD（默认 kline_daily 最新）")
    ap.add_argument("--days", type=int, default=5, help="趋势天数")
    ap.add_argument("--pool-limit", type=int, default=20, help="候选池只数")
    ap.add_argument("--missing-limit", type=int, default=20, help="缺失样例数")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    with db_conn() as conn:
        report = collect(conn, date=args.date, days=args.days,
                         pool_limit=args.pool_limit)
    report["missing_limit"] = args.missing_limit
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0 if all(t["fresh"] for t in report["targets"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
