#!/usr/bin/env bash
# 母侧纸盘 raw 收盘拉取（2026-11-21，修复工序 A1）。
#
# 为什么需要：`_snapshot_close_nav` 只认**当日 raw 收盘**（2026-11-16 D3 口径），
# 而 raw 原本只由独立版 :8080 的 `data fetch-raw` cron 供给；母侧
# `paper_trading/data.db.daily_bars_raw` 一直是空的 → `nav_history` 从 10-22 断档。
#
# 只拉**当前持仓**（NAV 只需要持仓的 raw 收盘），不做全市场：
# 15:50 收盘后一跳给 16:30 计划窗口用；20:40 再补一跳（源晚发布时兜底）。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PT="$ROOT/paper_trading"
PY="${PAPER_PY:-$ROOT/.venv/bin/python}"
DB="$PT/paper_account.db"

mkdir -p "$PT/logs"
if [ ! -x "$PY" ]; then
    echo "$(date '+%F %T') python 不存在：$PY" >&2
    exit 2
fi

syms=$(sqlite3 "$DB" "SELECT symbol FROM positions WHERE total_volume > 0" 2>/dev/null | tr '\n' ' ')
if [ -z "${syms// /}" ]; then
    echo "$(date '+%F %T') 无持仓，跳过 raw 拉取"
    exit 0
fi

cd "$PT" || exit 1
exec "$PY" -m paper_trading.cli \
    --data-db data.db --account-db paper_account.db \
    --lock-file var/fetch.lock \
    data fetch-raw --symbols $syms --days 60 --json \
    >> logs/raw_fetch.log 2>&1
