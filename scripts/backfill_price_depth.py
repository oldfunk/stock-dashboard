"""K 线历史深度回填：2016 → 2005（批13-a，2026-10-31）。

现状：全市场 5,193 只的 `kline_daily` 从 2016-01-01 起，2005–2015 十年空白，
回测跨不了完整牛熊周期（2007 / 2015 两轮）。本脚本逐只把**早段窗口**补齐。

只补缺口，不重写已有行
----------------------
每只票先查库内 `MIN(trade_date)`，只请求 `[--target-start, 那个最早日]` 这一段，
**写库时再严格只写早于它的行**（`trade_date < 已有最早日`）。已有的 2016+ 行
一个字都不动，所以本脚本可以反复跑、中途 kill 了再跑，都是收敛的。

overlap 那一天为什么"抓而不写"（本件的关键口径）
------------------------------------------------
派工单说"overlap 1 天即可"。这一天在这里不是用来重写的，而是**锚点桥 + 一致性检查**：

- `close` 是"后复权按本次抓取窗口最后一日缩放"出来的正数前复权
  （`price_model.merge_price_model`：`scale = raw[last]/adj[last]`，`last` 取**本窗口**末日）。
  如果顺手把 overlap 那天也写回去，它就从现在的锚点改成早段窗口的锚点 ——
  那一天会在序列里留下一个**台阶**，正是 `refresh_qfq_anchor.py`（批6-b）在修的那类问题。
  所以本脚本反过来用那天：`ratio = 库里 overlap 日的 close / 库里 overlap 日的 close_adj`，
  把整段早行按 `ratio` 统一缩放后再写 —— 早段与现网在接缝处**连续**，且窗口内相对收益不变。
- `close_raw`（不复权）与 `close_adj`（后复权）**与锚点无关**，原样写入：
  回测用的就是 `close_adj`，所以**深度补齐的正确性不依赖锚点**（派工单注①）；
  接缝比值 `库里close_adj vs 抓取close_adj` 另作一致性检查，>1% 记 `adj_mismatch` 并告警，
  不因为"能对上"就省掉。
- 全市场 qfq 锚点会随 `refresh_qfq_anchor` 的周期收敛而整体重锚；本脚本产出的早段行
  与当时的现网行同锚，之后由那条周期任务一起重锚，不会变成第二个锚点源。

上市晚于 target_start 的票
--------------------------
新浪对从未有过的日期返回空 → 记 `no_data`（语义就是"这只票在那段没有交易"，
不是限流；限流/异常走 `partial`，下一轮重试）。新股因此不会被反复重抓。

断点续传与护栏
--------------
进度表独立任务名 `price_depth`：`done`/`no_data` 跳过、`partial` 自动重试
（`--retry-no-data` / `--retry-partial` 可强制重跑），与 `price_history` 互不影响。
`--sleep` 默认 0.3s、`--limit/--offset` 分批、`--max-seconds` 预算、
`--max-consecutive-failures`（默认 5）连续失败熔断沿用（防上游抽风时把全市场标成失败）。
`--dry-run` 只报"会补多少只 / 预计多少行 / 接缝比值与 mismatch 情况"，一行都不写。

不直连生产：本脚本只交付代码 + 单测；实跑与调度由母方在 nec 执行
（预计 5,193 只 × 2 拉，过夜任务；与 `refresh_qfq_anchor --days 400` 的收敛跑错开）。

用法
----
    python scripts/backfill_price_depth.py --dry-run
    python scripts/backfill_price_depth.py --target-start 2005-01-01 --sleep 0.3
    python scripts/backfill_price_depth.py --limit 50 --offset 0     # 先小批试跑
    python scripts/backfill_price_depth.py --codes 600519,000001     # 显式点名必跑
    python scripts/backfill_price_depth.py --status                  # 只看进度
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from scripts.backfill_price_history import fetch_kline_sina_adjusted  # noqa: E402
from src.models.database import (  # noqa: E402
    db_conn, init_database, KlineDAO, FinancialHistoryDAO,
)

logger = logging.getLogger(__name__)

TASK = "price_depth"
DEFAULT_TARGET_START = "2005-01-01"
ADJ_MISMATCH_PCT = 1.0        # 接缝日 hfq 比值偏差告警阈值（%）——与锚点无关，超了就是数据不一致


def oldest_dates() -> list[tuple]:
    """``[(code, 库内最早交易日)]``（只含有 kline 行的票，按 code 升序）。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT code, MIN(trade_date) AS first_date FROM kline_daily"
            " GROUP BY code ORDER BY code").fetchall()
    return [(r["code"], r["first_date"]) for r in rows]


def pending_targets(targets: list[tuple], target_start: str, task: str = TASK,
                    limit: int | None = None, offset: int = 0,
                    retry_no_data: bool = False,
                    codes: list[str] | None = None) -> list[tuple]:
    """筛出还需补深度的票：已够深的直接剔掉，跑过的按进度表跳过。"""
    need = [(c, d) for c, d in targets if d and d > target_start]
    if codes is not None:
        picked = set(codes)
        return [(c, d) for c, d in targets if c in picked]
    skip = ["done"] + ([] if retry_no_data else ["no_data"])
    with db_conn() as conn:
        marks = ", ".join("?" for _ in skip)
        rows = conn.execute(
            f"SELECT code FROM backfill_progress WHERE task = ? AND status IN ({marks})",
            [task] + skip).fetchall()
    done = {r["code"] for r in rows}
    todo = [(c, d) for c, d in need if c not in done]
    return todo[offset:offset + limit] if limit else todo[offset:]


def seam_row(code: str, first_date: str):
    """取库里既有的最早一行（overlap 日）——锚点桥与一致性检查都靠它。"""
    with db_conn() as conn:
        return conn.execute(
            "SELECT close, close_adj FROM kline_daily"
            " WHERE code = ? AND trade_date = ?", (code, first_date)).fetchone()


def _qfq_ratio(stored) -> float:
    """早段行要乘上的缩放 = 现网接缝日的 `close / close_adj`（qfq 对 hfq 的比值）。

    比值拿不到（那天没有 close/close_adj）时返回 1.0 不缩放：与其猜一个锚点，
    不如按抓取窗口的自锚写进去，并在汇总里以 `no_seam_bridge` 看得见。
    """
    if stored is None:
        return 1.0
    close, adj = stored["close"], stored["close_adj"]
    if not close or not adj:
        return 1.0
    return close / adj


def backfill_one(code: str, first_date: str, target_start: str,
                 dry_run: bool = False, sleep: float = 0.0) -> dict:
    """补一只票的早段窗口 -> ``{status, added, seam_pct, note}``。"""
    rows = fetch_kline_sina_adjusted(code, target_start, end_date=first_date)
    if not rows:
        return {"status": "no_data", "added": 0, "seam_pct": None,
                "note": f"{target_start}..{first_date} 无行情（上市更晚或源无数据）"}
    earlier = [r for r in rows if r["trade_date"] < first_date]
    overlap = next((r for r in rows if r["trade_date"] == first_date), None)
    if not earlier:
        return {"status": "no_data", "added": 0, "seam_pct": None,
                "note": f"抓取窗口只覆盖到已有区间（{rows[0]['trade_date']} 起）"}
    stored = seam_row(code, first_date)
    pct = None
    if stored is not None and overlap is not None:
        if stored["close_adj"] and overlap["close_adj"]:
            pct = abs(overlap["close_adj"] / stored["close_adj"] - 1.0) * 100.0
        ratio = _qfq_ratio(stored)
        if ratio != 1.0:
            for r in earlier:
                for col in ("open", "close", "high", "low"):
                    if r.get(col) is not None:
                        r[col] = round(r[col] * ratio, 4)
    else:
        pct = None
    if not dry_run:
        KlineDAO().upsert_many(code, earlier)
    if sleep:
        time.sleep(sleep)
    return {"status": "done", "added": len(earlier), "seam_pct": pct,
            "note": f"{earlier[0]['trade_date']}..{earlier[-1]['trade_date']}"}


def run(target_start: str = DEFAULT_TARGET_START, limit: int | None = None,
        offset: int = 0, sleep: float = 0.3, dry_run: bool = False,
        max_seconds: float = 0.0, max_consecutive_failures: int = 5,
        retry_no_data: bool = False,
        codes: list[str] | None = None, task: str = TASK) -> dict:
    progress = FinancialHistoryDAO()
    all_targets = oldest_dates()
    todo = pending_targets(all_targets, target_start, task=task, limit=limit,
                           offset=offset, retry_no_data=retry_no_data,
                           codes=codes)
    already_deep = sum(1 for c, d in all_targets if d and d <= target_start)
    summary = {"task": task, "target_start": target_start,
               "codes_with_kline": len(all_targets),
               "already_deep": already_deep, "targets": len(todo),
               "done": 0, "no_data": 0, "partial": 0, "rows_added": 0,
               "seam_mismatch": 0, "no_seam_bridge": 0, "errors": [],
               "aborted": False, "budget_hit": False, "dry_run": dry_run}
    started = time.monotonic()
    consecutive = 0
    for idx, (code, first_date) in enumerate(todo, 1):
        try:
            res = backfill_one(code, first_date, target_start,
                               dry_run=dry_run, sleep=sleep)
        except Exception as e:  # noqa: BLE001 - 单只失败记 partial，可重试
            summary["partial"] += 1
            consecutive += 1
            summary["errors"].append([code, f"{type(e).__name__}: {str(e)[:100]}"])
            logger.warning("[深度回填] %s 失败（记 partial，可重试）: %s", code, e)
            if not dry_run:
                progress.mark_backfill_done(task, code, 0, status="partial",
                                            covered_report_date=None)
            if consecutive >= max_consecutive_failures:
                summary["aborted"] = True
                logger.error("[深度回填] 连续 %s 只失败，中止本轮", consecutive)
                break
            continue
        consecutive = 0
        summary[res["status"]] += 1
        summary["rows_added"] += res["added"]
        if res["seam_pct"] is None:
            summary["no_seam_bridge"] += 1
        elif res["seam_pct"] > ADJ_MISMATCH_PCT:
            summary["seam_mismatch"] += 1
            logger.warning("[深度回填] %s 接缝 hfq 偏差 %.2f%%（>%.1f%%）：%s",
                           code, res["seam_pct"], ADJ_MISMATCH_PCT, res["note"])
        if not dry_run:
            progress.mark_backfill_done(task, code, res["added"],
                                        status=res["status"],
                                        covered_report_date=first_date)
        if idx % 50 == 0:
            logger.info("[深度回填] %s/%s 已处理，新增 %s 行",
                        idx, len(todo), summary["rows_added"])
        if max_seconds and time.monotonic() - started >= max_seconds:
            summary["budget_hit"] = True
            logger.info("[深度回填] 时间预算用尽，优雅退出（下次续传）")
            break
    summary["remaining"] = (0 if codes else len(pending_targets(
        all_targets, target_start, task=task, retry_no_data=retry_no_data)))
    if not dry_run:
        summary["progress"] = progress.backfill_progress_summary(task)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="K 线历史深度回填（只补早段，不重写已有行）")
    ap.add_argument("--target-start", default=DEFAULT_TARGET_START,
                    help=f"目标最早日，默认 {DEFAULT_TARGET_START}")
    ap.add_argument("--codes", default=None, help="逗号分隔；显式指定即必跑")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--max-seconds", type=float, default=0.0, help="0=不限")
    ap.add_argument("--max-consecutive-failures", type=int, default=5)
    ap.add_argument("--retry-no-data", action="store_true")
    ap.add_argument("--retry-partial", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    init_database()
    if args.status:
        print(json.dumps(FinancialHistoryDAO().backfill_progress_summary(TASK),
                         ensure_ascii=False))
        return 0
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] if args.codes else None
    result = run(target_start=args.target_start, limit=args.limit,
                 offset=args.offset, sleep=args.sleep, dry_run=args.dry_run,
                 max_seconds=args.max_seconds,
                 max_consecutive_failures=args.max_consecutive_failures,
                 retry_no_data=args.retry_no_data, codes=codes)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if (result["partial"] or result["aborted"]) else 0


if __name__ == "__main__":
    sys.exit(main())
