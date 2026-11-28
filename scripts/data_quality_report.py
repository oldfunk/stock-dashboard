"""数据质量只读诊断（见 src/data_quality.py）。

用法：
    python scripts/data_quality_report.py
    python scripts/data_quality_report.py --days 10 --json --strict
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_quality import (daily_gaps, missing_months, month_range,  # noqa: E402
                              months_between, ratio)
from src.models.database import db_conn  # noqa: E402


def collect(days: int = 10) -> dict:
    with db_conn() as conn:
        hist = {r["m"]: int(r["n"]) for r in conn.execute(
            "SELECT substr(analysis_date,1,7) m, COUNT(*) n "
            "FROM stock_analysis_history GROUP BY m")}
        outc = {r["m"]: int(r["n"]) for r in conn.execute(
            "SELECT substr(analysis_date,1,7) m, COUNT(*) n "
            "FROM analysis_outcome GROUP BY m")}
        start, end = months_between(list(hist) + list(outc))
        missing = missing_months(set(hist) & set(outc), start, end) if start else []

        active = int(conn.execute(
            "SELECT COUNT(*) n FROM stock_snapshot WHERE market='A'"
        ).fetchone()["n"])
        open_dates = [r["trade_date"] for r in conn.execute(
            "SELECT trade_date FROM trading_calendar WHERE market_id='CN' "
            "AND is_open=1 AND trade_date <= date('now') "
            "ORDER BY trade_date DESC LIMIT ?", (int(days),))]
        counts = {r["trade_date"]: int(r["n"]) for r in conn.execute(
            "SELECT trade_date, COUNT(DISTINCT code) n FROM daily_close "
            "WHERE trade_date IN (%s) GROUP BY trade_date"
            % ",".join("?" * len(open_dates)), open_dates)} if open_dates else {}
        gaps = daily_gaps(open_dates, counts, active, 0.98)

        states = []
        for r in conn.execute(
                "SELECT trade_date, metrics_json, missing_json, coverage, status "
                "FROM market_state_daily ORDER BY trade_date DESC LIMIT ?",
                (int(days),)):
            try:
                metrics = json.loads(r["metrics_json"] or "{}")
            except ValueError:
                metrics = {}
            try:
                miss = json.loads(r["missing_json"] or "[]")
            except ValueError:
                miss = []
            states.append({"date": r["trade_date"], "coverage": r["coverage"],
                           "status": r["status"], "missing": miss,
                           "metrics": metrics})
        stale = (len(states) >= 2
                 and len({json.dumps(s["metrics"], sort_keys=True)
                          for s in states}) == 1)

        fin = conn.execute(
            "SELECT COUNT(*) n, SUM(roic_5y_avg IS NOT NULL) roic, "
            "SUM(fcf_5y_sum IS NOT NULL) fcf, "
            "SUM(roe_10y_avg IS NOT NULL) roe10 FROM financial_summary"
        ).fetchone()

    return {
        "monthly": {
            "history": hist,
            "outcome": outc,
            "missing_both": missing,
            "months": month_range(start, end) if start else [],
        },
        "daily_close": {"expect": active, "gaps": gaps,
                        "checked": open_dates},
        "market_state": {"rows": states, "stale_metrics": stale,
                         "pe_percentile_missing": sum(
                             1 for s in states
                             if "pe_percentile_5y" in (s.get("missing") or []) or
                             s.get("metrics", {}).get("pe_percentile_5y") is None)},
        "financial": {"rows": int(fin["n"]),
                      "roic_5y": int(fin["roic"] or 0),
                      "fcf_5y": int(fin["fcf"] or 0),
                      "roe_10y": int(fin["roe10"] or 0),
                      "roic_pct": ratio(int(fin["roic"] or 0), int(fin["n"] or 0)),
                      "fcf_pct": ratio(int(fin["fcf"] or 0), int(fin["n"] or 0)),
                      "roe10_pct": ratio(int(fin["roe10"] or 0), int(fin["n"] or 0))},
    }


def _pct(v) -> str:
    return "--" if v is None else f"{v * 100:.1f}%"


def render(rep: dict) -> str:
    lines = ["# 数据质量诊断", "", "## 1. 分析记录月度覆盖"]
    m = rep["monthly"]
    lines += ["| 月份 | stock_analysis_history | analysis_outcome |",
              "|---|---:|---:|"]
    for mon in m["months"]:
        lines.append(f"| {mon} | {m['history'].get(mon, 0)} | "
                     f"{m['outcome'].get(mon, 0)} |")
    lines += ["", "两边都缺的月份：" +
              ("、".join(m["missing_both"]) if m["missing_both"] else "无")]
    lines += ["", "## 2. daily_close 覆盖"]
    if rep["daily_close"]["gaps"]:
        for g in rep["daily_close"]["gaps"]:
            lines.append(f"- {g['date']}：{g['rows']}/{g['expect']}"
                         f"（{_pct(g['ratio'])}）")
    else:
        lines.append(f"最近 {len(rep['daily_close']['checked'])} 个交易日均 ≥98%。")
    lines += ["", "## 3. market_state_daily"]
    ms = rep["market_state"]
    lines.append(f"- 检查 {len(ms['rows'])} 天；pe_percentile_5y 缺失/为空 "
                 f"{ms['pe_percentile_missing']} 天；指标重复："
                 f"{'是' if ms['stale_metrics'] else '否'}")
    lines += ["", "## 4. 财务字段覆盖"]
    f = rep["financial"]
    lines.append(f"- financial_summary {f['rows']} 行；roic_5y {f['roic_5y']}"
                 f"（{_pct(f['roic_pct'])}）、fcf_5y {f['fcf_5y']}"
                 f"（{_pct(f['fcf_pct'])}）、roe_10y {f['roe_10y']}"
                 f"（{_pct(f['roe10_pct'])}）")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据质量只读诊断")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="有缺口时 exit 1")
    args = ap.parse_args(argv)
    rep = collect(days=args.days)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render(rep))
    problems = bool(rep["monthly"]["missing_both"]
                    or rep["daily_close"]["gaps"])
    return 1 if (args.strict and problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
