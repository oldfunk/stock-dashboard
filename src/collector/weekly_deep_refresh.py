"""周六深层字段全量刷新（批19-b，2026-11-12）。

修的是什么
----------
``src/orchestrator.py`` 旧写法 ``is_weekend_full = datetime.now().weekday() == 5``
是**死条件**：流水线只在工作日 15:30 跑（``scheduler._check_daily_pipeline`` 里
``weekday() >= 5`` 直接 return），所以 ``force_full`` 永远是 False，周末全量从未
发生（母方查 2026-11-07/08 周末日志无任何财务全量记录）。

派工单给了两个选项，本件选 **①挂到周六维护线程**，理由：
- 选项②（让流水线周六也跑）会把整条采集+筛选+AI 链路在周末再跑一遍，而周末没有
  新行情、没有新披露，代价远大于收益；
- 周六维护线程（``_run_weekly_refresh``）已经是后台 daemon 线程、已有进行中闸，
  本来就是"每周一次的慢活"该待的地方（N9 的落点）。

为什么必须分批 + 限时 + 进度表
------------------------------
全量模式下每只票约 6 个上游请求（同花顺骨架 + 东财利润表 + 现金流量表 +
roic/fcf + 资产负债表 + 现金流），产线候选池 771 只 → 约 4,600 个请求；实测单只
14-26s（000983 有 108 期），一次跑完要 3-5 小时。所以：
- ``em_batch``（默认 40 只）限量，``em_max_seconds``（默认 900s）限时；
- 进度表 ``em_full_gap`` 记 covered=最新报告期 → 下周六接着跑下一批，
  同一报告期只全量重拉一次，新报告期一到自动重新入选；
- 连续失败 5 只即中止（N10：上游限流时不能把全市场标成 partial）；
- 耗时/只数/剩余都写进日志与返回值，母方据此调批大小（派工单要的"测速"）。

两段顺序：先 ic 回填再 EM 全量
------------------------------
ic 段（``interest_coverage_backfill``）单只 1 个请求、约 0.4s，且是**唯一**能填
非金融股 ic 的路径（东财利润表 INTEREST_EXPENSE 对非金融股恒为空）；EM 段单只 6 个
请求。先便宜后贵，两段各有独立预算，EM 段不会把 ic 段的时间吃光。

目标集口径
----------
"全量"指**采集模式**（``force_full=True``：两张东财表无开关、不受平日冷却限制），
目标集仍按近 ``EM_DEEP_WINDOW`` 期的缺口挑（与平日 ``_em_sheets_needed`` 同一窗口，
老期缺口不绑架请求）。窗口外的老数据不在这里修：ic 的老期缺口由 ic 段走 mainfin
覆盖全部报告期，而 EM 那条 ``op/abs(ie)`` 对非金融股根本算不出 ic，重拉只是白烧配额。
"""

from __future__ import annotations

import logging
import time

from src.collector import interest_coverage_backfill as ic_backfill
from src.models.database import FinancialHistoryDAO

logger = logging.getLogger(__name__)

TASK_EM_FULL = "em_full_gap"
DEFAULT_IC_SECONDS = 1800.0     # ic 段：约 2,000 只（单只 ~0.8s 含限速）
DEFAULT_EM_SECONDS = 900.0      # EM 段：约 40-60 只（单只 14-26s）
DEFAULT_EM_BATCH = 40
DEFAULT_SLEEP = 0.4
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5


def _still_missing(dao: FinancialHistoryDAO, code: str, fields: tuple) -> bool:
    """这次拉完之后，该股**任一报告期**是否还缺这些字段（判 done / no_data）。"""
    periods = set(dao.get_report_dates([code]).get(code, []))
    filled = dao.get_deep_field_dates([code], fields).get(code, {})
    return any(periods - set(filled.get(f) or ()) for f in fields)


def run_em_full_refresh(limit: int = DEFAULT_EM_BATCH,
                        max_seconds: float = DEFAULT_EM_SECONDS,
                        sleep: float = DEFAULT_SLEEP,
                        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
                        task: str = TASK_EM_FULL) -> dict:
    """对近窗口仍缺深层字段的股票逐只 ``force_full=True`` 重拉（限量/限时/续跑）。"""
    from src.collector.akshare_fetcher import (
        EM_DEEP_FIELDS, EM_DEEP_WINDOW, collect_historical_financial_data,
    )

    dao = FinancialHistoryDAO()
    targets = dao.targets_missing_deep_fields(
        EM_DEEP_FIELDS, EM_DEEP_WINDOW, limit=limit, task=task)
    summary = {"task": task, "targets": len(targets), "processed": 0,
               "status_counts": {"done": 0, "partial": 0, "no_data": 0},
               "errors": [], "budget_hit": False, "aborted": False}
    started = time.monotonic()
    consecutive = 0
    for code, latest in targets:
        error = None
        try:
            got = collect_historical_financial_data([{"code": code}], force_full=True)
        except Exception as e:  # noqa: BLE001 - 单只失败不中止整批
            got, error = {}, f"{type(e).__name__}: {e}"
        if error or code not in got:
            # 骨架没出数 = 这次没取到（不是"上游没有"）→ partial，不写 covered
            status = "partial"
            error = error or "骨架无数据（这次没取到）"
        elif _still_missing(dao, code, EM_DEEP_FIELDS):
            status = "no_data"
        else:
            status = "done"
        summary["processed"] += 1
        summary["status_counts"][status] += 1
        if error:
            consecutive += 1
            summary["errors"].append([code, error])
            logger.warning("[周刷新EM] %s 未完成：%s", code, error)
        else:
            consecutive = 0
        dao.mark_backfill_done(task, code, updated_rows=0, status=status,
                               covered_report_date=(
                                   None if status == "partial" else latest))
        if consecutive >= max_consecutive_failures:
            summary["aborted"] = True
            logger.error("[周刷新EM] 连续 %s 只失败，中止"
                         "（防上游限流时把全市场标成 partial）", consecutive)
            break
        if sleep:
            time.sleep(sleep)
        if max_seconds and time.monotonic() - started >= max_seconds:
            summary["budget_hit"] = True
            break

    summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
    summary["remaining"] = len(dao.targets_missing_deep_fields(
        EM_DEEP_FIELDS, EM_DEEP_WINDOW, task=task))
    summary["progress"] = dao.backfill_progress_summary(task)
    logger.info("[周刷新EM] 目标 %s 只、处理 %s 只（done %s / partial %s / no_data %s），"
                "剩余 %s 只，耗时 %ss",
                summary["targets"], summary["processed"],
                summary["status_counts"]["done"], summary["status_counts"]["partial"],
                summary["status_counts"]["no_data"], summary["remaining"],
                summary["elapsed_seconds"])
    return summary


def run_weekly_deep_refresh(cfg: dict | None = None) -> dict:
    """周六维护入口：ic 回填 + EM 全量重拉，预算从 ``deep_refresh`` 配置读。

    ``em_batch=0`` 即"这段不跑"（上游限流时只留 ic 段），不需要另加开关。
    """
    conf = ((cfg or {}).get("deep_refresh") or {})
    sleep = float(conf.get("sleep", DEFAULT_SLEEP))
    ic = ic_backfill.run(
        max_seconds=float(conf.get("ic_max_seconds", DEFAULT_IC_SECONDS)),
        sleep=sleep)
    em = run_em_full_refresh(
        limit=int(conf.get("em_batch", DEFAULT_EM_BATCH)),
        max_seconds=float(conf.get("em_max_seconds", DEFAULT_EM_SECONDS)),
        sleep=sleep)
    logger.info("[周刷新] 深层字段：ic 填 %s 行（剩余 %s 只）；EM 全量 %s 只（剩余 %s 只）",
                ic.get("filled"), ic.get("remaining"),
                em.get("processed"), em.get("remaining"))
    return {"ic": ic, "em": em}