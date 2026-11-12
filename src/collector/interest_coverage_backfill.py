"""利息保障倍数（``interest_coverage``）缺口回填——批19-c（2026-11-12）。

为什么单独一件，而不是扩 ``scripts/backfill_financial_extra.py``
--------------------------------------------------------------
派工单给了两条路，选"专门脚本"的理由是三条硬约束（都在 nec 只读取证过）：

1. **那条路径写不进这一列**：``backfill_financial_extra`` 落库走
   ``FinancialHistoryDAO.update_extra_fields``，它 ``WHERE report_type='A'``
   且列白名单 ``EXTRA_FIELDS`` 里**没有 interest_coverage**。派工单点名的缺口
   恰恰是 2026 三个**季报**报告期——扩它得同时改写入范围（A→A+Q）和列白名单，
   等于把另一件已验收的回填口径改掉，风险比新写一件大。
2. **加利润表源修不了这个缺口**：东财利润表 ``INTEREST_EXPENSE`` 对非金融股恒为空
   （605259 实测 0/31 期、000983 0/108 期），采集器那条 ``op / abs(ie)`` 算不出
   ic。19-a 让这些股票进 todo 也只是白发请求。
3. **口径必须与库内既有值一致**：现有 34,509 条 ic 里 34,508 条与东财主要财务指标
   ``INTSTCOVRATE`` **逐位相同**（真正的历史写入方是 ``backfill_financial_full.py``
   走的 mainfin，不是采集器）。所以本件也走 mainfin，**只填 ic 这一列**——
   不顺带填 fcf/roic：fcf 在库里有两种口径（采集器 ``经营现金流+投资现金流``
   vs 上游 ``FCFF_BACK``），混进同一列会把历史序列变成两种算法的拼接。

逻辑放 src 不放 scripts：``src`` 不允许反向 import ``scripts``
（``tests/test_no_src_imports_scripts.py`` 守着），而 19-b 要把这件挂进周六维护
线程（``src/scheduler.py::_run_weekly_refresh``）。``trading_calendar`` 就是同一
先例：src 里放逻辑，scripts 里只留薄 CLI。

写入范围与安全性
----------------
- 只填 NULL（``fill_interest_coverage``），已有值不覆盖 → 幂等、重跑安全；
- 年报 + 季报都填，但**只更新库里已存在的行**，绝不造半截行；
- 进度表任务名 ``interest_coverage_backfill``（与 ``financial_extra`` /
  ``notice_date_quarterly`` / ``em_deep_gap`` 分开，互不影响入选集）；
  ``covered_report_date`` 存该股最新报告期：新报告期一到自动重新入选；
  ``partial`` 不写 covered，下次自动重试；
- ``--max-consecutive-failures``（默认 5）连续失败即中止：上游限流时不能把全市场
  标成 partial（N10 的教训）；
- ``--dry-run`` 与真实写入共用同一个谓词，预测不会漂移。

节奏：一次请求即取回该股全部历史（``ps=100`` × 最多 3 页），单只约 0.4s +
``--sleep``（默认 0.4s）。产线目标集规模见交付贴的只读实测。

不直连生产：本件只交付代码 + 单测；实跑与是否进周刷由母方在 nec 决定。

用法
----
    python scripts/backfill_interest_coverage.py --dry-run
    python scripts/backfill_interest_coverage.py --limit 50 --sleep 0.4
    python scripts/backfill_interest_coverage.py --codes 600519,000001 --force
    python scripts/backfill_interest_coverage.py --status
"""

from __future__ import annotations

import logging
import time

from src.collector.mainfin_fetcher import fetch_mainfin_interest_coverage
from src.models.database import FinancialHistoryDAO

logger = logging.getLogger(__name__)

TASK = "interest_coverage_backfill"
DEFAULT_SLEEP = 0.4


def apply_code(dao: FinancialHistoryDAO, code: str, dry_run: bool = False) -> dict:
    """取一只票全部报告期的 ic 并回填；返回该只结果（异常不外抛，折进 error）。"""
    fetched = fetch_mainfin_interest_coverage(code)
    filled = (dao.fill_interest_coverage(code, fetched["values"], dry_run=dry_run)
              if fetched["values"] else 0)
    if fetched["error"]:
        status = "partial"
    elif not fetched["values"]:
        status = "no_data"
    else:
        status = "done"
    return {"code": code, "status": status, "filled": filled,
            "values": len(fetched["values"]), "rows": fetched["rows"],
            "error": fetched["error"]}


def run(codes: list[str] | None = None, limit: int | None = None,
        offset: int = 0, sleep: float = DEFAULT_SLEEP, dry_run: bool = False,
        max_seconds: float = 0.0, max_consecutive_failures: int = 5,
        force: bool = False, task: str = TASK) -> dict:
    """按目标集逐只回填。进度表决定"跑过就不再跑"，``codes`` 显式指定即必跑。"""
    dao = FinancialHistoryDAO()
    if codes:
        targets = [(c, None) for c in codes]
    else:
        targets = dao.targets_missing_interest_coverage(
            limit=limit, offset=offset, task=None if force else task)

    summary = {"task": task, "targets": len(targets), "processed": 0,
               "filled": 0, "values": 0, "rows": 0, "dry_run": dry_run,
               "status_counts": {"done": 0, "partial": 0, "no_data": 0},
               "errors": [], "budget_hit": False, "aborted": False}
    started = time.monotonic()
    consecutive = 0
    for code, latest in targets:
        result = apply_code(dao, code, dry_run=dry_run)
        summary["processed"] += 1
        summary["filled"] += result["filled"]
        summary["values"] += result["values"]
        summary["rows"] += result["rows"]
        summary["status_counts"][result["status"]] += 1
        if result["error"]:
            consecutive += 1
            summary["errors"].append([code, result["error"]])
            logger.warning("[ic回填] %s 未完成：%s", code, result["error"])
        else:
            consecutive = 0
        if not dry_run:
            dao.mark_backfill_done(task, code, updated_rows=result["filled"],
                                   status=result["status"],
                                   covered_report_date=(
                                       None if result["status"] == "partial"
                                       else latest))
        if consecutive >= max_consecutive_failures:
            summary["aborted"] = True
            logger.error("[ic回填] 连续 %s 只失败，中止"
                         "（防上游限流时把全市场标成 partial）", consecutive)
            break
        if sleep:
            time.sleep(sleep)
        if max_seconds and time.monotonic() - started >= max_seconds:
            summary["budget_hit"] = True
            break

    summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
    # 剩余 = 回填后仍在目标集里的票数（刚跑完的已被进度表排除，不再减 processed）
    summary["remaining"] = (
        0 if codes else
        len(dao.targets_missing_interest_coverage(task=None if force else task)))
    if not dry_run:
        summary["progress"] = dao.backfill_progress_summary(task)
    logger.info("[ic回填] 目标 %s 只、处理 %s 只、填 %s 行（done %s / partial %s /"
                " no_data %s），剩余 %s 只，耗时 %ss",
                summary["targets"], summary["processed"], summary["filled"],
                summary["status_counts"]["done"],
                summary["status_counts"]["partial"],
                summary["status_counts"]["no_data"], summary["remaining"],
                summary["elapsed_seconds"])
    return summary