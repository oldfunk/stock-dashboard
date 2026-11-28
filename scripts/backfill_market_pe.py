"""回填市场 PE 历史（5 年，年报 EPS/PIT 口径）。

默认 dry-run 只打印统计；`--publish` 写 `market_pe_history`。
用法：
    python scripts/backfill_market_pe.py --years 5
    python scripts/backfill_market_pe.py --years 5 --publish
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.database import db_conn  # noqa: E402
from src.research.market_pe import (compute_history, default_range,  # noqa: E402
                                    percentile_5y, save_history)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回填市场 PE 历史")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--end")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    start, end = default_range(args.years, args.end)
    with db_conn() as conn:
        active = conn.execute(
            "SELECT COUNT(*) n FROM stock_snapshot WHERE market='A'"
        ).fetchone()["n"]
        rows = compute_history(conn, start, end, active)
        if args.publish:
            save_history(conn, rows)
        pct = percentile_5y(conn, end) if args.publish else None
        if args.json:
            print(json.dumps({"start": start, "end": end,
                              "rows": len(rows), "last": rows[-3:],
                              "percentile": pct}, ensure_ascii=False, indent=2))
        else:
            print(f"{start} → {end}: {len(rows)} 个交易日")
            for r in rows[-3:]:
                print(f"  {r['trade_date']} PE中位 {r['pe_median']} "
                      f"样本 {r['sample_size']} 覆盖 {r['coverage']}")
            print(f"当前 5 年分位：{pct}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
