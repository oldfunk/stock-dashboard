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

周期接线与节奏（批20-b/c，2026-11-12）
--------------------------------------
入口 `run_weekly_qfq_anchor(cfg)` 挂在 `scheduler._run_weekly_refresh` 里，位置在
`refresh_outcomes` **之前**：前向收益用 kline 的 `close` 算
（`analyzer/outcomes.py`）且算出即冻结不重算，所以必须先把价格口径统一到本窗口
锚点，新冻结的收益才不会带台阶。预算从 `config.yaml` 的 `qfq_anchor` 段读
（`days`/`batch`/`max_seconds`/`sleep`/`max_consecutive_failures`），改配置不改代码。

目标集**资格**不变，但**排序改成漂移优先**（多锚点的票先修）——理由与产线实测
数字见 `pending_rows` 的 docstring。这是限量预算下"先修真实污染"的调度顺序，
不是口径变更。
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from src.collector.akshare_fetcher import SourceUnsupportedError
from src.models.database import FinancialHistoryDAO, KlineDAO, db_conn

logger = logging.getLogger("qfq_anchor")

TASK = "qfq_anchor"

DEFAULT_DAYS = 90
DEFAULT_BATCH = 60
DEFAULT_MAX_SECONDS = 900.0
DEFAULT_SLEEP = 1.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5


def window_end(conn) -> str | None:
    """当前锚点 = 库里最新的交易日。"""
    row = conn.execute("SELECT MAX(trade_date) AS d FROM kline_daily").fetchone()
    return row["d"] if row else None


def window_start(end_date: str, days: int) -> str:
    return (date.fromisoformat(end_date) - timedelta(days=days)).isoformat()


def pending_rows(conn, end_date: str, days: int, limit: int | None = None,
                 offset: int = 0, force: bool = False) -> list[tuple[str, int]]:
    """待重锚的 ``(code, scales)``，**漂移优先**排序（批20-b）。

    资格判据（与批6-b 一字不变）：**窗口内有任意行情行**、且进度没覆盖到本次锚点日。
    用"窗口内有行"而不是"今天有行"——停牌数十天但窗口内有成交的票，它窗口内的 qfq
    同样要跟到新锚，漏掉就会在窗口里留下台阶。`force` 忽略进度表；`limit/offset`
    供分批与人工错峰。

    为什么按 scales 降序（这是"节奏"，不是改资格）
    ----------------------------------------------
    `scales` = 窗口内 ``close/close_adj`` 的不同取值个数 = **该股带了几种锚点**。
    收益 ``close[t]/close[t-1]`` 会把同一个 scale 约掉，所以**单一锚点的票根本没有
    污染**，重锚它只是整段乘一个常数、收益率一字不变；真正有害的是同一只票序列里
    出现多个 scale（批次边界那个台阶）。

    产线只读实测（2026-11-12，锚点 2026-11-11、90 天窗口）：5,514 只里
    **只有 24 只多 scale**，且正是每日增量在写的那批；5,490 只单 scale。
    而一次性全量重锚实测 4h51m / 5,513 只（≈3.17s/只）。所以在限量预算下，
    把多 scale 的票排在前面＝用同样的请求量先修掉**全部**真实污染。
    该聚合查询实测 1.53s（281,028 行 → 5,514 组），每周一次可接受。

    ``close_adj`` 缺失/非正的行不计入 scale（NULL 不被 COUNT DISTINCT 计数），
    因此这类票 scales=0 排在最后，但**仍在目标集里**——不会被静默漏掉。
    """
    sql = ("SELECT k.code, COUNT(DISTINCT CASE WHEN k.close > 0 AND k.close_adj > 0"
           " THEN ROUND(k.close / k.close_adj, 6) END) AS scales"
           " FROM kline_daily k WHERE k.trade_date >= ? AND k.trade_date <= ? ")
    params: list = [window_start(end_date, days), end_date]
    if not force:
        sql += ("AND NOT EXISTS (SELECT 1 FROM backfill_progress p"
                " WHERE p.task = ? AND p.code = k.code"
                " AND p.status IN ('done', 'no_data')"
                " AND COALESCE(p.covered_report_date, '') >= ?) ")
        params += [TASK, end_date]
    sql += " GROUP BY k.code ORDER BY scales DESC, k.code"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return [(r["code"], r["scales"])
            for r in conn.execute(sql, params).fetchall()]


def pending_codes(conn, end_date: str, days: int, limit: int | None = None,
                  offset: int = 0, force: bool = False) -> list[str]:
    """只要代码列表（批6-b 起的公开签名，保持不变）。"""
    return [c for c, _ in pending_rows(conn, end_date, days, limit=limit,
                                       offset=offset, force=force)]


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
    skipped（源能力边界，如北交所；**不写进度表**）/
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
    except SourceUnsupportedError as e:
        # 批13-d 同一口径：结构性跳过既不是 partial（每轮白等 5/10/20s 退避），
        # 也不是 no_data（会被进度表永久排除，堵死"换源重跑"）。产线窗口内有
        # 320 只北交所，Sina 供不了——不这样记账，周六维护每周白烧 3.1 小时。
        logger.info("[qfq锚点] %s 跳过（源能力边界）：%s", code, e)
        return {"code": code, "status": "skipped", "rows": 0,
                "window": [start, end], "error": f"{type(e).__name__}: {e}"}
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


def run(days: int = DEFAULT_DAYS, codes: list[str] | None = None,
        dry_run: bool = False, limit: int | None = None, offset: int = 0,
        sleep: float = DEFAULT_SLEEP, force: bool = False, fetch=None,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES) -> dict:
    """批量重锚（限量/限时/熔断/进度表续跑）。`fetch` 可注入（单测传假 Sina）。

    调用方负责建表（CLI 侧 `init_database()`；调度器路径由应用启动时建好）。

    节奏（批20-b，与批19-b 的 `em_batch`/`em_max_seconds` 同一套语义）：
    - `limit`：本次最多几只；`max_seconds`：本次时间预算，到点即停；
    - `max_consecutive_failures`：连续失败几只即中止（N10：上游抽风时不能把
      全市场标成 partial）；**结构性跳过不计入熔断**（批13-d）；
    - 停在哪都行：done 写 covered=锚点日 → 同锚点下次跳过；partial 不写 covered
      → 下次自动重试。所以"限量/限时"不会丢目标，只是把活摊到下一班车。
    """
    if fetch is None:
        from src.collector.sina_price import fetch_kline_sina_adjusted
        fetch = fetch_kline_sina_adjusted

    fh = FinancialHistoryDAO()
    with db_conn() as conn:
        end = window_end(conn)
        if not end:
            return {"anchor": None, "targets": 0, "done": 0, "partial": 0,
                    "written": 0, "skipped": 0, "skipped_unsupported": 0,
                    "drifted_targets": None, "budget_hit": False,
                    "aborted": False, "elapsed_seconds": 0.0, "remaining": 0,
                    "error": "kline_daily 无数据"}
        # 显式 codes = "我就要这几只"：不查进度表，必跑（也就不统计漂移）
        if codes:
            targets, drifted = list(codes), None
        else:
            pairs = pending_rows(conn, end, days, limit=limit, offset=offset,
                                 force=force)
            targets = [c for c, _ in pairs]
            drifted = sum(1 for _, s in pairs if s > 1)

    summary = {"anchor": end, "window": [window_start(end, days), end],
               "targets": len(targets), "done": 0, "partial": 0,
               "written": 0, "skipped": 0, "skipped_unsupported": 0,
               "drifted_targets": drifted, "budget_hit": False,
               "aborted": False, "dry_run": bool(dry_run)}
    started = time.monotonic()
    consecutive = 0
    for code in targets:
        r = refresh_code(code, days, fetch, dry_run=dry_run, end_date=end)
        if r["status"] == "dry_run":
            summary["skipped"] += 1          # 只统计目标集，不写库也不记进度
            summary["written"] += r["rows"]
            continue
        if r["status"] == "skipped":
            summary["skipped_unsupported"] += 1
            continue                         # 不写进度、不计熔断、不睡退避
        if r["status"] == "done":
            summary["done"] += 1
            summary["written"] += r["rows"]
            consecutive = 0
        else:
            summary["partial"] += 1
            consecutive += 1
            logger.warning("[qfq锚点] %s 未完成：%s", code, r["error"])
        # partial 不写 covered_report_date → 下次运行自动重新入选
        fh.mark_backfill_done(TASK, code, updated_rows=r["rows"],
                              status=r["status"],
                              covered_report_date=(end if r["status"] == "done"
                                                   else None))
        if max_consecutive_failures and consecutive >= max_consecutive_failures:
            summary["aborted"] = True
            logger.error("[qfq锚点] 连续 %s 只失败，中止"
                         "（防上游限流时把全市场标成 partial）", consecutive)
            break
        if sleep:
            time.sleep(sleep)
        if max_seconds and time.monotonic() - started >= max_seconds:
            summary["budget_hit"] = True
            logger.info("[qfq锚点] 时间预算 %ss 用尽，本次停在第 %s 只",
                        max_seconds, summary["done"] + summary["partial"])
            break

    summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
    summary["progress"] = fh.backfill_progress_summary(TASK)
    with db_conn() as conn:
        summary["remaining"] = len(pending_codes(conn, end, days, force=force))
    logger.info("[qfq锚点] 锚点 %s：目标 %s 只（多锚 %s）、重锚 %s 只 / partial %s / "
                "源不支持 %s，写 %s 行，剩余 %s 只，耗时 %ss%s",
                end, summary["targets"], drifted, summary["done"],
                summary["partial"], summary["skipped_unsupported"],
                summary["written"], summary["remaining"],
                summary["elapsed_seconds"],
                "（预算用尽）" if summary["budget_hit"] else
                "（连续失败中止）" if summary["aborted"] else "")
    return summary


def run_weekly_qfq_anchor(cfg: dict | None = None) -> dict:
    """周六维护入口：滚动窗口重锚，预算从 ``qfq_anchor`` 配置读（批20-c）。

    ``batch=0`` 即"这段不跑"（上游限流/要给别的活让路时用），不需要另加开关——
    与 ``deep_refresh.em_batch=0`` 同一约定。
    """
    conf = ((cfg or {}).get("qfq_anchor") or {})
    batch = int(conf.get("batch", DEFAULT_BATCH))
    if batch <= 0:
        logger.info("[qfq锚点] 配置 batch=%s，本段跳过", batch)
        return {"skipped_by_config": True, "anchor": None, "targets": 0,
                "done": 0, "partial": 0, "written": 0, "remaining": None}
    return run(days=int(conf.get("days", DEFAULT_DAYS)),
               limit=batch,
               max_seconds=float(conf.get("max_seconds", DEFAULT_MAX_SECONDS)),
               sleep=float(conf.get("sleep", DEFAULT_SLEEP)),
               max_consecutive_failures=int(conf.get(
                   "max_consecutive_failures", DEFAULT_MAX_CONSECUTIVE_FAILURES)))