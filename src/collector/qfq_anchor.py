"""qfq 锚点周期重锚（批6-b 首版在 scripts；批20-a 上移 src 层，2026-11-12）。

背景（为什么要重锚）
-------------------
`close` / `close_qfq` 是"后复权按最新价缩放"出来的正数前复权：
`price_model.merge_price_model` 里 `scale = raw[last] / adj[last]`，而 `last` 取
**本次抓取窗口的最后一日**（不是全历史最后一日）。前复权锚点随每次除权除息移动，
而日常增量只写新日期、不重写历史行 → 库里不同批次写入的 qfq 值各带各的锚点，
**同一只票的序列里出现台阶**，跨批次的收益率/回撤被基准漂移污染。

为什么在 src 而不在 scripts
---------------------------
周六维护线程（`scheduler._run_weekly_refresh`）要周期调用它，而 src 不得反向
import scripts（`tests/test_no_src_imports_scripts.py` 棘轮）。与 `price_model`
（U9-A）、`trading_calendar`（P2-1）同一处置：逻辑在 src，脚本侧只留薄 CLI +
同名 re-export。**本模块不调用 `init_database()`**（沿用 `trading_calendar` 先例）：
建表是 CLI/应用启动的职责，库函数不该有副作用。

本模块做什么
------------
按 code 重抓近 `days`（默认 90 个自然日）的 Sina 不复权 + 后复权，经同一个
`merge_price_model` 合流后 upsert，使**窗口内**所有行统一到本次锚点
（= 抓取交集末日）。`close_qfq` 由 `KlineDAO.upsert_many` 强制镜像 `close`
（Hermes P0-1），这里不自行拼接，避免出现第二个口径来源。

部分重锚（如实声明，别把它当全历史重锚用）
------------------------------------------
窗口外的更早历史行**保持它们当初写入时的锚点**，本模块不触碰。因此若除权发生在
窗口内，窗口边界处仍会留下一个台阶；要全历史同锚，必须让 `days` 覆盖到该股最早
一天。周期任务按滚动窗口设计：漂移被限制在边界附近，且 `close_raw`（不复权）与
`close_adj`（后复权）**与锚点无关**，不受重锚影响。

列保护（重写不得把已有值抹成 NULL）
-----------------------------------
`upsert_many` 的 ON CONFLICT 对 open/close/high/low/volume/amount 是**无条件覆写**
（只有 turnover/close_raw/close_adj/shares 走 COALESCE）。抓取偶发缺字段时直接写
会抹掉现网数值，所以先把窗口内现有行读出来当底，只把**抓到的非 None 值**覆盖上去。

失败姿态（fail-closed）
-----------------------
Sina 双拉抛异常或返回空 → 该股**一行都不写**，状态记 `partial`，绝不写半套数据
去覆盖现有 close。

幂等与断点
----------
进度复用 `backfill_progress`（task 名 `qfq_anchor`，主键 (task, code)），
`covered_report_date` 存**本次窗口末日（锚点日）**：同一锚点重复执行会被跳过
（`force` 强制重跑）；新交易日到来后 `covered < 新锚点` 自动重新入选，不会被
进度表永久锁死——与年报回填同一套语义。`partial` 不写 covered，所以下次运行自动
重试。显式 `codes` 表示"就要这几只"，不查进度表。
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from src.models.database import FinancialHistoryDAO, KlineDAO, db_conn

logger = logging.getLogger("qfq_anchor")

TASK = "qfq_anchor"


def window_end(conn) -> str | None:
    """当前锚点 = 库里最新的交易日。"""
    row = conn.execute("SELECT MAX(trade_date) AS d FROM kline_daily").fetchone()
    return row["d"] if row else None


def window_start(end_date: str, days: int) -> str:
    return (date.fromisoformat(end_date) - timedelta(days=days)).isoformat()


def pending_codes(conn, end_date: str, days: int, limit: int | None = None,
                  offset: int = 0, force: bool = False) -> list[str]:
    """待重锚代码：**窗口内有任意行情行**、且进度没覆盖到本次锚点日的。

    判据用"窗口内有行"而不是"今天有行"——停牌数十天但窗口内有成交的票，
    它窗口内的 qfq 同样要跟到新锚，漏掉就会在窗口里留下台阶。
    `force` 忽略进度表全量重跑；`limit/offset` 供分批与人工错峰。
    """
    sql = ("SELECT DISTINCT k.code FROM kline_daily k "
           "WHERE k.trade_date >= ? AND k.trade_date <= ? ")
    params: list = [window_start(end_date, days), end_date]
    if not force:
        sql += ("AND NOT EXISTS (SELECT 1 FROM backfill_progress p"
                " WHERE p.task = ? AND p.code = k.code"
                " AND p.status IN ('done', 'no_data')"
                " AND COALESCE(p.covered_report_date, '') >= ?) ")
        params += [TASK, end_date]
    sql += " ORDER BY k.code"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return [r["code"] for r in conn.execute(sql, params).fetchall()]


def existing_window_rows(code: str, start: str, end: str) -> dict[str, dict]:
    """取窗口内现有行，作为"抓取缺字段时的兜底值"。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM kline_daily WHERE code = ? AND trade_date >= ? "
            "AND trade_date <= ? ORDER BY trade_date",
            (code, start, end)).fetchall()
    return {r["trade_date"]: dict(r) for r in rows}


def merge_with_existing(fetched: list[dict], existing: dict[str, dict]) -> list[dict]:
    """抓到的非 None 值覆盖到现有行上；两边都没有的列保持 None（不臆造）。

    保证不会因为某次抓取缺 open/high/low 就把线上已有值抹成 NULL——那几列
    `upsert_many` 是无条件覆写。没有收盘价的行直接跳过（宁缺勿错）。
    """
    out = []
    for r in fetched:
        d = r.get("trade_date")
        if not d or r.get("close") is None:
            continue
        base = existing.get(d, {})
        rec = {"trade_date": d}
        for col in ("open", "close", "high", "low", "volume", "amount",
                    "turnover", "close_raw", "close_adj", "shares"):
            val = r.get(col)
            rec[col] = val if val is not None else base.get(col)
        out.append(rec)
    return out


def refresh_code(code: str, days: int, fetch, dry_run: bool = False,
                 end_date: str | None = None) -> dict:
    """重锚一只票的窗口。返回 {code, status, rows, window, error}。

    status：done（已重写）/ partial（抓取失败或返回空，**未写库**）/
    no_data（库里取不到锚点日）/ dry_run（只统计不写）。
    """
    with db_conn() as conn:
        end = end_date or window_end(conn)
    if not end:
        return {"code": code, "status": "no_data", "rows": 0,
                "window": None, "error": "kline_daily 无数据，取不到锚点日"}
    start = window_start(end, days)
    if dry_run:
        with db_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM kline_daily WHERE code = ? "
                "AND trade_date >= ? AND trade_date <= ?",
                (code, start, end)).fetchone()["n"]
        return {"code": code, "status": "dry_run", "rows": int(n),
                "window": [start, end], "error": ""}
    try:
        fetched = fetch(code, start)
    except Exception as e:  # noqa: BLE001 - 抓取失败不得覆盖现有数据
        logger.warning("[qfq锚点] %s 抓取失败，保持原值：%s", code, e)
        return {"code": code, "status": "partial", "rows": 0,
                "window": [start, end], "error": f"{type(e).__name__}: {e}"}
    if not fetched:
        return {"code": code, "status": "partial", "rows": 0,
                "window": [start, end], "error": "Sina 返回空行，未覆盖"}
    rows = merge_with_existing(fetched, existing_window_rows(code, start, end))
    if not rows:
        return {"code": code, "status": "partial", "rows": 0,
                "window": [start, end], "error": "合流后无有效行（全无收盘价）"}
    written = KlineDAO().upsert_many(code, rows)
    return {"code": code, "status": "done", "rows": int(written),
            "window": [start, end], "error": ""}


def run(days: int = 90, codes: list[str] | None = None,
        dry_run: bool = False, limit: int | None = None, offset: int = 0,
        sleep: float = 1.0, force: bool = False, fetch=None) -> dict:
    """批量重锚。`fetch` 可注入（单测传假 Sina；默认用真实抓取函数）。

    调用方负责建表（CLI 侧 `init_database()`；调度器路径由应用启动时建好）。
    """
    if fetch is None:
        from src.collector.sina_price import fetch_kline_sina_adjusted
        fetch = fetch_kline_sina_adjusted

    fh = FinancialHistoryDAO()
    with db_conn() as conn:
        end = window_end(conn)
        if not end:
            return {"anchor": None, "targets": 0, "done": 0, "partial": 0,
                    "written": 0, "skipped": 0, "error": "kline_daily 无数据"}
        # 显式 codes = "我就要这几只"：不查进度表，必跑
        targets = list(codes) if codes else pending_codes(
            conn, end, days, limit=limit, offset=offset, force=force)

    summary = {"anchor": end, "window": [window_start(end, days), end],
               "targets": len(targets), "done": 0, "partial": 0,
               "written": 0, "skipped": 0, "dry_run": bool(dry_run)}
    for code in targets:
        r = refresh_code(code, days, fetch, dry_run=dry_run, end_date=end)
        if r["status"] == "dry_run":
            summary["skipped"] += 1          # 只统计目标集，不写库也不记进度
            summary["written"] += r["rows"]
            continue
        if r["status"] == "done":
            summary["done"] += 1
            summary["written"] += r["rows"]
        else:
            summary["partial"] += 1
            logger.warning("[qfq锚点] %s 未完成：%s", code, r["error"])
        # partial 不写 covered_report_date → 下次运行自动重新入选
        fh.mark_backfill_done(TASK, code, updated_rows=r["rows"],
                              status=r["status"],
                              covered_report_date=(end if r["status"] == "done"
                                                   else None))
        if sleep:
            time.sleep(sleep)
    summary["progress"] = fh.backfill_progress_summary(TASK)
    return summary