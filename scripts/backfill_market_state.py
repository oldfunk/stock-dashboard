"""market_state_daily 历史回填（批9-a，"把数据都搞好"专项）。

背景：为什么库里只有 13 行
--------------------------
`market_state_daily` 的写入入口只有手工 CLI `scripts/research/snapshot_market.py`。
实测（grep src/ 全量）每日流水线里没有任何地方写它——`src/research/llm/tools.py`
与 `src/web/data_api.py` 只**读**市场状态。所以行数 = 历史上手跑过的那几天，
不会自己长。本脚本负责把过去 N 天补齐；要不要进排程是母方的决定，不在本件。

已有 CLI 缺的两件事
------------------
`snapshot_market.py --start --end` 已能按日重算并落库，且天然幂等
（`market_state_daily.trade_date` 是主键，DAO 走 INSERT OR REPLACE）。但它
(1) 要调用方先知道日期区间，而回填的口径是"最近 N 个交易日"；
(2) 没有续传概念：60 天跑到第 40 天断了，重跑会把前 40 天白算一遍（结果无害，
    只是浪费）。本脚本只补这两条，计算口径一律复用 src 侧函数，不另起炉灶。

窗口口径
--------
`--days N`（默认 60）= `kline_daily` 里 `<= --end`（缺省取库内最新交易日）的
最近 N 个**有行情行的**交易日。没有 kline 行的日期不进窗口：那种日子算出来的
向量必然大面积 NULL，写进目标表只是污染 regime 样本（口径选择，不是偷懒）。
窗口在开跑时一次定死，循环中途新落库的交易日不会让窗口滑动——续传语义要靠
"同一个窗口跑到完"才成立（有用例守）。

幂等与续传
----------
- 幂等：同一日重算即覆盖（主键 + INSERT OR REPLACE），不追加重复行。
- 续传：进度直接以**目标表本身**为准——窗口里已有行的日期跳过。不另立
  `backfill_progress`：回填单元是"天"（默认 60 次本地计算），用第三张表记账
  换来的续传能力有限，却会把"只写目标表"这条边界破掉。
  代价如实写明：源表事后被修正过的日子**不会自动失效**，要重算得显式 `--force`
  （与 `refresh_qfq_anchor.py` 的 `--force` 同语义）。

只读源表 / 到底写哪几张表
------------------------
`kline_daily`、`financial_history`、`stock_snapshot` 一律只读；单日取数还跑在
`PRAGMA query_only=ON` 的单连接事务里（批9-a1 / 审计 P2-3），所以一份向量内部
不会出现"写前半段 + 写后半段"的跨时点拼接。
本脚本的写只有两处：`market_state_daily`（目标表）与 `data_quality_daily`
（`snapshot_day` 的既有行为：dataset='market_state_daily' 的质量记录，R0-5 质量
门按 dataset 读它——回填日若不写，就会在质量视图里凭空缺席）。派工单写的是
"只写目标表"，我按"目标表 + 它自己的质量记录"这个口径执行；若要严格单表，验收
时点出来，我把质量记录做成开关，不改计算逻辑。

不吞异常
--------
某日抛异常就让它冒出来（进程退出，该日不写）。理由：字段缺失本来就走 NULL（不
补数），能抛的只有实现坏了或库坏了；把坏日子静默跳过，等于用一个"回填完成"的
数字盖住一个真实的坏。已写过的日子留在库里，下次运行自动从断点继续。

不直连生产：本脚本只交付代码 + 单测；实跑与调度由母方在 nec 执行。

用法
----
    python scripts/backfill_market_state.py --dry-run
    python scripts/backfill_market_state.py                     # 最近 60 个交易日
    python scripts/backfill_market_state.py --days 250 --end 2026-10-28
    python scripts/backfill_market_state.py --force             # 重算窗口内全部
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from src.research.dao import ResearchDAO  # noqa: E402
from src.research.market_state import (  # noqa: E402
    recent_trading_dates, snapshot_day,
)

DEFAULT_DAYS = 60


def run(days: int = DEFAULT_DAYS, end: str | None = None,
        force: bool = False, dry_run: bool = False) -> dict:
    """回填窗口内的市场状态向量；返回汇总（不含异常——异常直接外抛）。"""
    if days <= 0:
        raise ValueError(f"--days 须为正整数，得到 {days}")
    window = recent_trading_dates(days, end)
    if not window:
        return {"error": "kline_daily 没有可用交易日（先跑行情回填再来）",
                "window": 0, "computed": 0, "skipped": 0,
                "dry_run": dry_run}
    dao = ResearchDAO()
    # 续传：目标表里已有的日期即"已完成"（--force 忽略它，全窗口重算）
    have = set() if force else set(dao.get_state_dates(window[0], window[-1]))
    todo = [d for d in window if d not in have]
    status: dict[str, int] = {}
    for day in todo:
        vector = snapshot_day(day, dry_run=dry_run, dao=dao)
        status[vector.status] = status.get(vector.status, 0) + 1
    return {"days": days, "window_start": window[0], "window_end": window[-1],
            "window": len(window), "skipped": len(window) - len(todo),
            "computed": len(todo), "status": status, "dry_run": dry_run}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="market_state_daily 历史回填（按最近 N 个交易日，续传 + 幂等）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"回填最近 N 个有行情的交易日，默认 {DEFAULT_DAYS}")
    ap.add_argument("--end", default=None,
                    help="窗口右端（含）；缺省=kline_daily 最新交易日")
    ap.add_argument("--force", action="store_true",
                    help="忽略目标表已有行，整个窗口重算（源数据被修正后用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只算不写（两张目标表都不碰）")
    args = ap.parse_args(argv)
    try:
        result = run(days=args.days, end=args.end, force=args.force,
                     dry_run=args.dry_run)
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
