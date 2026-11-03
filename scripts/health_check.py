"""数据健康巡检（H1-c，Hermes 2026-11-03）。

只读：不写库、不调 init_database。输出当日数据健康快照，供 cron/人工每日跑。
用法：
    python scripts/health_check.py            # 人类可读
    python scripts/health_check.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from src.utils import now_cn  # noqa: E402
from src.models.database import db_conn  # noqa: E402


def run() -> dict:
    today = now_cn().date().isoformat()
    out = {"date": today}
    with db_conn() as conn:
        out["kline_daily_rows"] = conn.execute(
            "SELECT COUNT(*) AS n FROM kline_daily").fetchone()["n"]
        out["kline_daily_null_close_qfq"] = conn.execute(
            "SELECT COUNT(*) AS n FROM kline_daily WHERE close_qfq IS NULL").fetchone()["n"]
        out["kline_daily_zero_close"] = conn.execute(
            "SELECT COUNT(*) AS n FROM kline_daily WHERE close <= 0").fetchone()["n"]
        out["daily_close_today"] = conn.execute(
            "SELECT COUNT(*) AS n FROM daily_close WHERE trade_date = ?", (today,)).fetchone()["n"]
        try:
            row = conn.execute(
                "SELECT status, coverage, breadth_n FROM market_state_daily WHERE trade_date = ?",
                (today,)).fetchone()
            out["market_state_today"] = dict(row) if row else None
        except Exception:
            out["market_state_today"] = None
        try:
            out["sector_coverage_pct"] = round(
                100.0 * conn.execute(
                    "SELECT COUNT(*) AS n FROM stock_snapshot WHERE sector IS NOT NULL AND sector <> ''"
                ).fetchone()["n"] / max(1, conn.execute(
                    "SELECT COUNT(*) AS n FROM stock_snapshot").fetchone()["n"]), 1)
        except Exception:
            out["sector_coverage_pct"] = None
        try:
            out["notice_date_q_null"] = conn.execute(
                "SELECT COUNT(*) AS n FROM financial_history WHERE report_type='Q' "
                "AND (notice_date IS NULL OR TRIM(notice_date)='')").fetchone()["n"]
        except Exception:
            out["notice_date_q_null"] = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = run()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        for k, v in r.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
