"""季报披露日回填（批10-a，2026-10-29；AGENTS 纪律：先取证再动手）。

派工单的第一条要求是"选定数据源前先用 2-3 只票实测该源给的是披露日而非报告期末"，
下面是这次实测本身（nec 只读，命令见交付帖，原始输出摘录）。

一、数据源给的是**披露日**，不是报告期末
----------------------------------------
源：东财 datacenter ``RPT_F10_FINANCE_MAINFINADATA`` 的 ``NOTICE_DATE``
（与仓内 ``mainfin_fetcher.fetch_mainfin_annual`` 同一个接口同一个字段）。
2026-10-29 实测 3 只票，逐行看 ``REPORT_DATE`` vs ``NOTICE_DATE``：

    600519  REPORT_DATE=2026-03-31 REPORT_TYPE='一季报' NOTICE_DATE=2026-04-25
    600519  REPORT_DATE=2025-09-30 REPORT_TYPE='三季报' NOTICE_DATE=2025-10-30
    600519  REPORT_DATE=2026-06-30 REPORT_TYPE='中报'   NOTICE_DATE=2026-08-15
    000001  REPORT_DATE=2026-09-30 REPORT_TYPE='三季报' NOTICE_DATE=2026-10-24
    000001  REPORT_DATE=2025-03-31 REPORT_TYPE='一季报' NOTICE_DATE=2025-04-19
    300750  REPORT_DATE=2026-03-31 REPORT_TYPE='一季报' NOTICE_DATE=2026-04-16
    300750  REPORT_DATE=2025-12-31 REPORT_TYPE='年报'   NOTICE_DATE=2026-03-10

每季都晚于期末，且间隔正好处在法定披露窗口内（一季报 4 月内、中报 8 月内、
三季报 10 月内、年报次年 4 月内）——**是公告日口径**，可以直接进 ``notice_date``。
抽样完整率：20 只票共 1,177 条季报行，``NOTICE_DATE`` 缺失 0、占位/格式坏 0、
早于报告期 0。（清洗闸门仍保留，见下。）

二、一个会静默吃掉整个回填的坑（所以这条取证值得做）
----------------------------------------------------
东财 ``REPORT_TYPE`` 的实际取值是 **'一季报' / '中报' / '三季报' / '年报'——没有
'季报' 这个值**。照 ``fetch_mainfin_annual`` 的写法加 `(REPORT_TYPE="季报")` 过滤，
600519 / 000001 各实测一次，**都是 0 行**：那样写不报错，只会稳定"回填 0 行"，
看起来像跑了、实际什么都没填。所以本件不加类型过滤，在代码里按三类季报名筛
（``QUARTERLY_REPORT_TYPES``）。

三、为什么只填 NULL、只碰 Q 行
------------------------------
现网缺口（同一次只读 SQL）：``report_type='Q'`` 52,038 行，notice_date 为空
**52,038 行（100%）**；``report_type='A'`` 93,309 行只空 2.4%（那条路径已有
``backfill_financial_extra.py``）。所以本件的写范围就是派工单点名的那一格：
`UPDATE ... WHERE report_type='Q' AND notice_date 为空`——
不碰年报行（年报有自己的口径），不覆盖已有值（幂等，重跑安全），
库里没有的报告期命中 0 行（绝不因回填造出半截行）。

四、清洗闸门（``clean_notice_date``）
------------------------------------
``notice_date`` 是 PIT 的前视闸门（仓内多处口径是"缺 notice 不参与、不回退
report_date"），填错等于把未来数据提前放行。所以宁可少填：非 ``YYYY-MM-DD``、
年份早于 2000、**早于报告期**的一律不写，并计数报在 ``rejected`` 里。
20 只票抽样中这道闸门一次都没触发——留着是因为上游哪天开始发占位值要先看得见。

五、进度、节奏与 fail-closed
----------------------------
- 进度表任务名 ``notice_date_quarterly``（与 ``financial_extra`` / ``qfq_anchor`` 分开，
  互不影响入选集）。``covered_report_date`` 存**该股最新季报报告期**：新季报一到
  自动重新入选，不会被进度表永久锁死；``partial`` 不写 covered，下次自动重试。
- 一次请求即取回该股全部历史（实测 ``ps=100`` 时 600519 的 103 行有 100 行在第一页），
  目标集是"有 Q 行且 notice 为空"的 1,007 只，单只约 0.4s + ``--sleep``（默认 0.4s）
  -> 全量约 13 分钟。
- ``--max-consecutive-failures``（默认 5）连续失败即中止：东财限流时不能把全市场
  标成 partial（N10 的教训），中止比硬撑到 1,000 只更省时间也更好诊断。
- ``--dry-run`` 只数"这次能填几行"不写库（与真实写入共用同一个谓词，预测不会漂移）。

不直连生产：本脚本只交付代码 + 单测；实跑与是否进周刷由母方在 nec 决定。

用法
----
    python scripts/backfill_notice_date_quarterly.py --dry-run
    python scripts/backfill_notice_date_quarterly.py --limit 50 --sleep 0.4
    python scripts/backfill_notice_date_quarterly.py --codes 600519,000001 --force
    python scripts/backfill_notice_date_quarterly.py --status
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

from src.collector.mainfin_fetcher import fetch_mainfin_quarterly_notices  # noqa: E402
from src.models.database import FinancialHistoryDAO, init_database  # noqa: E402

logger = logging.getLogger(__name__)

TASK = "notice_date_quarterly"
DEFAULT_SLEEP = 0.4


def apply_code(dao: FinancialHistoryDAO, code: str, dry_run: bool = False) -> dict:
    """取一只票的季报披露日并回填；返回该只的结果（异常不外抛，折进 error）。"""
    fetched = fetch_mainfin_quarterly_notices(code)
    filled = dao.fill_quarterly_notice_dates(
        code, fetched["notices"], dry_run=dry_run) if fetched["notices"] else 0
    if fetched["error"]:
        status = "partial"
    elif not fetched["notices"]:
        status = "no_data"
    else:
        status = "done"
    return {"code": code, "status": status, "filled": filled,
            "notices": len(fetched["notices"]), "rejected": fetched["rejected"],
            "error": fetched["error"]}


def run(codes: list[str] | None = None, limit: int | None = None,
        offset: int = 0, sleep: float = DEFAULT_SLEEP, dry_run: bool = False,
        max_seconds: float = 0.0, max_consecutive_failures: int = 5,
        force: bool = False, task: str = TASK) -> dict:
    """按目标集逐只回填。进度表决定"跑过就不再跑"，``--codes`` 显式指定即必跑。"""
    dao = FinancialHistoryDAO()
    if codes:
        targets = [(c, None) for c in codes]
    else:
        targets = dao.targets_missing_notice_quarterly(
            limit=limit, offset=offset, task=None if force else task)

    summary = {"task": task, "targets": len(targets), "processed": 0,
               "filled": 0, "notices": 0, "rejected": 0, "dry_run": dry_run,
               "status_counts": {"done": 0, "partial": 0, "no_data": 0},
               "errors": [], "budget_hit": False, "aborted": False}
    started = time.monotonic()
    consecutive = 0
    for code, latest_q in targets:
        result = apply_code(dao, code, dry_run=dry_run)
        summary["processed"] += 1
        summary["filled"] += result["filled"]
        summary["notices"] += result["notices"]
        summary["rejected"] += result["rejected"]
        summary["status_counts"][result["status"]] += 1
        if result["error"]:
            consecutive += 1
            summary["errors"].append([code, result["error"]])
            logger.warning("[季报披露日] %s 未完成：%s", code, result["error"])
        else:
            consecutive = 0
        # partial 不写 covered -> 下次运行自动重新入选；done/no_data 记到当前最新季报
        if not dry_run:
            dao.mark_backfill_done(task, code, updated_rows=result["filled"],
                                   status=result["status"],
                                   covered_report_date=(
                                       None if result["status"] == "partial" else latest_q))
        if consecutive >= max_consecutive_failures:
            summary["aborted"] = True
            logger.error("[季报披露日] 连续 %s 只失败，中止（防上游限流时把全市场标成 partial）",
                         consecutive)
            break
        if sleep:
            time.sleep(sleep)
        if max_seconds and time.monotonic() - started >= max_seconds:
            summary["budget_hit"] = True
            break

    # 剩余 = 回填后仍在目标集里的票数（刚跑完的已被进度表排除，不再减 processed）
    summary["remaining"] = (
        0 if codes else
        len(dao.targets_missing_notice_quarterly(task=None if force else task)))
    if not dry_run:
        summary["progress"] = dao.backfill_progress_summary(task)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="季报 notice_date 回填（只填 report_type='Q' 且 notice_date 为 NULL 的行）")
    ap.add_argument("--codes", default=None, help="逗号分隔代码；显式指定即必跑")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                    help=f"每只之间的间隔秒，默认 {DEFAULT_SLEEP}")
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="本次时间预算（秒），0=不限")
    ap.add_argument("--max-consecutive-failures", type=int, default=5,
                    help="连续失败多少只即中止，默认 5")
    ap.add_argument("--force", action="store_true",
                    help="忽略进度表，目标集重新全跑")
    ap.add_argument("--dry-run", action="store_true", help="不写库，只报这次能填几行")
    ap.add_argument("--status", action="store_true", help="只看进度汇总，不跑")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    init_database()
    dao = FinancialHistoryDAO()
    if args.status:
        print(json.dumps(dao.backfill_progress_summary(TASK), ensure_ascii=False))
        return 0
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] if args.codes else None
    result = run(codes=codes, limit=args.limit, offset=args.offset,
                 sleep=args.sleep, dry_run=args.dry_run,
                 max_seconds=args.max_seconds,
                 max_consecutive_failures=args.max_consecutive_failures,
                 force=args.force)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if (result["status_counts"]["partial"] or result["aborted"]) else 0


if __name__ == "__main__":
    sys.exit(main())
