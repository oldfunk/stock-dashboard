"""母侧 T96 前置：盘后二次拉取持仓 + 候选池的完整 OHLC（A2，2026-11-24）。

为什么需要：T96 要求计划窗口"持仓当日 K 线"；15:30 流水线的 K 线拉取常遇到
腾讯当日 bar 未发布（当日 0 行），16:30 计划窗口会按 `no-fresh-data` 重试。
17:05 / 17:35 再拉一次（腾讯/东财此时通常已有当日 bar），让 16:30–18:30
窗口里后续的 tick 能拿到当日数据。

只写母库 `kline_daily`（完整 OHLC + close_raw/adj），不碰纸盘账本、不下单。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collector.akshare_fetcher import (  # noqa: E402
    SourceUnsupportedError, fetch_kline_data,
)
from src.models.database import KlineDAO, db_conn  # noqa: E402
from src.paper_bridge import load_positions  # noqa: E402


def target_codes(limit: int = 20) -> list[str]:
    """持仓（必刷）+ 最新筛选 TopN（T96 覆盖率）。"""
    held = []
    pos = load_positions()
    for p in (pos.get("data") or []):
        if int(p.get("total_volume") or 0) > 0:
            held.append(str(p.get("symbol")).zfill(6))
    pool: list[str] = []
    with db_conn() as conn:
        row = conn.execute(
            "SELECT run_id FROM screening_result GROUP BY run_id "
            "ORDER BY MAX(run_date) DESC, run_id DESC LIMIT 1").fetchone()
        if row:
            pool = [str(r["code"]).zfill(6) for r in conn.execute(
                "SELECT code FROM screening_result WHERE run_id = ? "
                "ORDER BY score DESC LIMIT ?", (row["run_id"], int(limit)))]
    return list(dict.fromkeys(held + pool))


def main() -> int:
    codes = target_codes()
    if not codes:
        print("无持仓/候选，跳过")
        return 0
    dao = KlineDAO()
    added = skipped = failed = 0
    for code in codes:
        try:
            latest = dao.get_latest_date(code)
            records = fetch_kline_data(code, start_date=latest)
        except SourceUnsupportedError:
            skipped += 1
            continue
        except Exception as e:                      # noqa: BLE001
            failed += 1
            print(f"{code} 拉取失败: {type(e).__name__}: {e}")
            continue
        if not records:
            continue
        try:
            added += int(dao.upsert_many(code, records) or 0)
        except Exception as e:                      # noqa: BLE001
            failed += 1
            print(f"{code} 写库失败: {type(e).__name__}: {e}")
    print({"codes": len(codes), "rows": added,
           "skipped_unsupported": skipped, "failed": failed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
