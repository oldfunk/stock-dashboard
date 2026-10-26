"""全市场年报历史回填（2026-10-26，P5-2）。

背景：`financial_history` 只有 959 只有年报（候选/观察池历史上被采过），
全市场 5527 只里约 4500 只从未采过。没有年报 → 无法估值、无法回测、无法分位。

数据源（每只 3 类请求，均为东财直连）：
- 主要财务指标：一次取回全部年报的 roe/eps/毛利率/净利率/增长/负债率/ROIC/FCF/
  披露日/净资产/总资产/总股本（2026-10-26 扩展）
- 资产负债表：cash / 有息负债 / 商誉 / 总资产 / 净资产
- 现金流量表：capex / 经营现金流

控制流量：逐只 `--sleep`（默认 0.4s）、`--limit`、`--max-seconds`、单实例锁；
进度写 `backfill_progress(task='financial_full')`，可断点续跑/重试无数据。

用法：
  python scripts/backfill_financial_full.py --dry-run
  python scripts/backfill_financial_full.py --limit 200 --sleep 0.4
  python scripts/backfill_financial_full.py --until-done --sleep 0.4
  python scripts/backfill_financial_full.py --status
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from src.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from src.models.database import (
    FinancialHistoryDAO, db_conn, get_db_path, init_database,
)

TASK = "financial_full"


def build_records(code: str, mainfin: dict | None, balance: dict | None,
                  cashflow: dict | None) -> list[dict]:
    """合并三源为年报记录（只保留 12-31；total_share → total_shares）。"""
    merged: dict[str, dict] = {}
    for src in (mainfin or {}, balance or {}, cashflow or {}):
        for rpt, vals in src.items():
            if not str(rpt).endswith("12-31"):
                continue
            bucket = merged.setdefault(str(rpt), {})
            for k, v in (vals or {}).items():
                if v is None:
                    continue
                bucket["total_shares" if k == "total_share" else k] = v
    records = []
    for rpt, vals in merged.items():
        if not vals:
            continue
        rec = {"stock_code": code, "report_date": rpt}
        rec.update(vals)
        records.append(rec)
    return sorted(records, key=lambda r: r["report_date"])


def _retry(fn, *args, attempts: int = 6, **kwargs):
    """DB 操作重试：WAL 下仍可能撞上长写事务/checkpoint，不能一次锁就崩。"""
    last = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e).lower() or i == attempts - 1:
                raise
            wait = min(30, 2 ** (i + 1))
            logger.warning(f"[full] DB 锁等待 {wait}s 后重试（{e}）")
            time.sleep(wait)
    raise last  # type: ignore[misc]


def target_codes(all_codes: bool = False) -> list[str]:
    """默认只补没有年报的股票；--all 覆盖全市场（更新模式）。"""
    sql = "SELECT code FROM stock_snapshot"
    if not all_codes:
        sql += (" WHERE code NOT IN (SELECT DISTINCT stock_code"
                " FROM financial_history WHERE report_type='A')")
    sql += " ORDER BY code"
    with db_conn() as conn:
        return [r["code"] for r in conn.execute(sql).fetchall()]


def pending_codes(codes: list[str], retry_no_data: bool = False) -> list[str]:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT code, status FROM backfill_progress WHERE task = ?",
            (TASK,)).fetchall()
    skip = {r["code"] for r in rows
            if r["status"] == "done" or
            (r["status"] == "no_data" and not retry_no_data)}
    return [c for c in codes if c not in skip]


def _acquire_lock():
    try:
        import fcntl
    except ImportError:
        return None
    lock_path = os.path.join(os.path.dirname(get_db_path()),
                             "backfill_financial_full.lock")
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def _safe(fn, code, name):
    try:
        return fn(code) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[full] {code} {name} 失败: {e}")
        return {}


def _process(dao: FinancialHistoryDAO, codes: list[str], args,
             started: float, totals: dict) -> bool:
    from src.collector.balance_sheet_fetcher import fetch_balance_sheet_annual
    from src.collector.cashflow_fetcher import fetch_cash_flow_annual
    from src.collector.mainfin_fetcher import fetch_mainfin_annual

    for code in codes:
        if args.max_seconds and (time.monotonic() - started) > args.max_seconds:
            logger.info(f"[full] 达到软预算 {args.max_seconds}s，优雅退出")
            return True
        mf = _safe(fetch_mainfin_annual, code, "mainfin")
        bs = _safe(fetch_balance_sheet_annual, code, "balance")
        cf = _safe(fetch_cash_flow_annual, code, "cashflow")
        records = build_records(code, mf, bs, cf)
        n = 0
        if records:
            _retry(dao.batch_save, records)
            n = len(records)
            totals["updated_rows"] += n
        if not records:
            status = "no_data"
            totals["no_data"] += 1
        elif not mf:
            # 有资产负债表/现金流但没有基础指标 → 半截数据，可重试
            status = "partial"
            totals["partial"] += 1
        else:
            status = "done"
        _retry(dao.mark_backfill_done, TASK, code, n, status)
        totals["processed"] += 1
        if totals["processed"] % 20 == 0:
            logger.info(f"[full] 累计 {totals['processed']} 只"
                        f"（行 {totals['updated_rows']}，无数据 {totals['no_data']}，"
                        f"部分 {totals['partial']}）")
        if args.sleep:
            time.sleep(args.sleep)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场年报历史回填")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--sleep", type=float, default=0.4)
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--until-done", action="store_true")
    parser.add_argument("--batch-sleep", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--retry-no-data", action="store_true")
    parser.add_argument("--all", action="store_true",
                        help="覆盖全市场（默认只补没有年报的股票）")
    parser.add_argument("--codes", default="", help="指定代码，逗号分隔")
    args = parser.parse_args()

    init_database()
    dao = FinancialHistoryDAO()

    if args.status:
        print(json.dumps(dao.backfill_progress_summary(TASK), ensure_ascii=False))
        return 0

    explicit = [c.strip() for c in args.codes.split(",") if c.strip()]
    all_targets = explicit or _retry(target_codes, args.all)
    if args.dry_run:
        codes = all_targets
        print(json.dumps({"target_codes": len(codes),
                          "pending": len(_retry(pending_codes, codes))},
                         ensure_ascii=False))
        return 0

    lock = _acquire_lock()
    if lock is False:
        print(json.dumps({"skipped": "another financial full backfill is running"},
                         ensure_ascii=False))
        return 0

    started = time.monotonic()
    totals = {"processed": 0, "updated_rows": 0, "no_data": 0, "partial": 0}
    rounds = 0
    while True:
        if explicit and rounds == 0:
            codes = explicit
        else:
            codes = _retry(
                pending_codes, all_targets,
                retry_no_data=args.retry_no_data and rounds == 0)
            codes = codes[:args.limit] if args.limit else codes
        if not codes:
            break
        rounds += 1
        logger.info(f"[full] 第 {rounds} 批：{len(codes)} 只")
        if _process(dao, codes, args, started, totals):
            break
        if explicit or not args.until_done:
            break
        remaining = len(_retry(pending_codes, all_targets))
        if remaining == 0:
            break
        logger.info(f"[full] 批间停顿 {args.batch_sleep}s（剩余 {remaining}）")
        time.sleep(args.batch_sleep)

    elapsed = round(time.monotonic() - started, 1)
    print(json.dumps({
        **totals,
        "rounds": rounds,
        "elapsed_sec": elapsed,
        "progress": dao.backfill_progress_summary(TASK),
        "remaining": len(_retry(pending_codes, all_targets)),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
