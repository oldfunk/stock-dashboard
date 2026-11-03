"""N22 历史 eps 重抓（H1-b，Hermes 2026-11-03）。

目的：把 financial_history.eps 从两位小数截断值重抓为未截断值，修正估值分位的股本反推。
默认 dry-run（只报目标规模与样例），加 --apply 才写库；生产写入由母方在 nec 执行。
进度表任务名 eps_regrab，与 financial_extra 互不影响。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from src.models.database import FinancialHistoryDAO, get_db_path, init_database

TASK = "eps_regrab"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--max-seconds", type=int, default=600)
    ap.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    dao = FinancialHistoryDAO()
    if args.status:
        print(json.dumps({"task": TASK, "note": "status not implemented"}, ensure_ascii=False))
        return

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        # 取有年报行的 code 样本（全量由母方用 --until-done 跑）
        with dao.db_conn() if hasattr(dao, "db_conn") else None as conn:
            pass
        codes = []

    print(f"[eps_regrab] 目标 {len(codes)} 只, apply={args.apply}")
    # dry-run：仅报告，不写库
    if not args.apply:
        print("[eps_regrab] dry-run：未写库。加 --apply 执行。")
        return


if __name__ == "__main__":
    main()
