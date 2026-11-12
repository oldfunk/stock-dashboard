"""qfq 锚点周期重写 —— 薄 CLI（批6-b 首版；批20-a 逻辑上移 src，2026-11-12）。

逻辑本体在 ``src/collector/qfq_anchor.py``（为什么重锚、部分重锚的边界、列保护、
fail-closed、幂等与断点语义都在那份 docstring 里，此处不复制第二份口径）。
上移理由：周六维护线程要周期调用它，而 src 不得反向 import scripts
（``tests/test_no_src_imports_scripts.py``）。与 ``trading_calendar``（P2-1）、
``price_model``（U9-A）同一处置。

本文件只负责三件事：建表（``init_database``）、解析 argv、把结果打成 JSON。
同名 re-export 保住批6-b 的 10 条回归用例与既有 import 路径。

不直连生产：实跑与调度由母方在 nec 执行；周期路径见
``scheduler._run_weekly_refresh`` → ``qfq_anchor.run_weekly_qfq_anchor``。

用法
----
    python scripts/refresh_qfq_anchor.py --dry-run
    python scripts/refresh_qfq_anchor.py --days 90 --sleep 1.0
    python scripts/refresh_qfq_anchor.py --codes 600519,000792 --force
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from src.models.database import init_database  # noqa: E402
# 逻辑已上移 src 层（批20-a）；保留同名导入使旧引用与单测不破
from src.collector.qfq_anchor import (  # noqa: E402,F401
    TASK, existing_window_rows, merge_with_existing, pending_codes,
    refresh_code, run, window_end, window_start,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="qfq 锚点周期重写（只重写窗口内行）")
    ap.add_argument("--days", type=int, default=90,
                    help="窗口天数（自然日），默认 90")
    ap.add_argument("--codes", default=None,
                    help="逗号分隔代码；显式指定即必跑（不查进度表）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="每只之间的间隔秒（限流），默认 1.0")
    ap.add_argument("--force", action="store_true",
                    help="忽略进度表，已重锚过的也重跑")
    ap.add_argument("--dry-run", action="store_true",
                    help="不发 HTTP、不写库，只报目标集与行数")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    init_database()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] \
        if args.codes else None
    result = run(days=args.days, codes=codes, dry_run=args.dry_run,
                 limit=args.limit, offset=args.offset, sleep=args.sleep,
                 force=args.force)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("partial", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())