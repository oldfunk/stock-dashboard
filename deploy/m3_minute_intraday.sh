#!/usr/bin/env bash
# M3 盘中分钟采集：补当日分钟线（tick 有 bar 可用；幂等）
# 生产安装位置：/home/nec/paper-trading/deploy/m3_minute_intraday.sh
# cron：6,21,36,51 9-15 * * 1-5（与 rq tick 的 5 分钟节奏错开）
# 注：2026-11-19 纳管——此前只在 NEC 上存在（未跟踪），一次 `git clean -fd`
# 就会静默断掉盘中供给，而 tick 端只会表现为"分钟线没接上"。
cd /home/nec/paper-trading || exit 1
SYMS=$(sqlite3 data.db "SELECT symbol FROM stock_pool ORDER BY symbol")
/home/nec/venvs/rq-engine/bin/python -m paper_trading.cli minute sync \
    --intraday --symbols $SYMS --json >> logs/minute_intraday.log 2>&1
