"""Research OS schema 迁移脚本（2026-10-26，R0-2）。

- 创建研究表（`init_database()` 会幂等创建）。
- 回填 `kline_daily.market='CN'`、`instrument_id='CN:'||code`（分批，避免长锁）。
- 从 `stock_snapshot` 生成 `instrument`（A 股）。

用法：
  python scripts/migrate_research_schema.py --dry-run
  python scripts/migrate_research_schema.py
"""

import argparse
import json
import sys

PROJ = __import__("pathlib").Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.models.database import db_conn, init_database, now_cn

BATCH = 20000


def backfill_kline_instrument(dry_run: bool = False) -> int:
    updated = 0
    while True:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM kline_daily WHERE instrument_id IS NULL"
            ).fetchone()
            total = row["n"]
            if total == 0:
                break
            if dry_run:
                return total
            cur = conn.execute(
                "UPDATE kline_daily SET market = 'CN', instrument_id = 'CN:' || code "
                "WHERE instrument_id IS NULL AND rowid IN "
                "(SELECT rowid FROM kline_daily WHERE instrument_id IS NULL LIMIT ?)",
                (BATCH,))
            updated += cur.rowcount
            if updated % (BATCH * 5) == 0:
                print(f"[migrate] kline 已回填 {updated} 行（剩余 {total - updated}）")
    return updated


def seed_instruments(dry_run: bool = False) -> int:
    ts = now_cn().isoformat()
    sql = """
        INSERT OR REPLACE INTO instrument
        (instrument_id, market_id, symbol, exchange, name, currency,
         lot_size, tick_size, list_date, delist_date, status, created_at, updated_at)
        SELECT 'CN:' || code, 'CN', code,
               CASE WHEN code LIKE '6%' OR code LIKE '9%' THEN 'SSE'
                    WHEN code LIKE '0%' OR code LIKE '3%' THEN 'SZSE'
                    ELSE 'BSE' END,
               name, 'CNY', NULL, NULL, list_date, NULL,
               CASE WHEN is_st = 1 THEN 'st' ELSE 'active' END, ?, ?
        FROM stock_snapshot
    """
    if dry_run:
        with db_conn() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM stock_snapshot").fetchone()["n"]
    with db_conn() as conn:
        return conn.execute(sql, (ts, ts)).rowcount


def migrate(dry_run: bool = False) -> dict:
    init_database()
    kline_total = 0
    with db_conn() as conn:
        kline_total = conn.execute(
            "SELECT COUNT(*) AS n FROM kline_daily").fetchone()["n"]
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM kline_daily WHERE instrument_id IS NULL"
        ).fetchone()["n"]
    print(f"[migrate] kline 总行 {kline_total}，instrument_id 为空 {before}")
    updated = backfill_kline_instrument(dry_run=dry_run)
    if dry_run:
        instruments = seed_instruments(dry_run=True)
        return {
            "kline_total": kline_total,
            "pending": before,
            "would_backfill": updated,
            "would_seed_instruments": instruments,
        }
    seeded = seed_instruments(dry_run=False)
    with db_conn() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM kline_daily WHERE instrument_id IS NULL"
        ).fetchone()["n"]
        instruments = conn.execute(
            "SELECT COUNT(*) AS n FROM instrument").fetchone()["n"]
    return {
        "kline_total": kline_total,
        "backfilled": updated,
        "remaining_null": remaining,
        "instruments": instruments,
        "seeded_instruments": seeded,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Research OS schema 迁移")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(dry_run=args.dry_run), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
