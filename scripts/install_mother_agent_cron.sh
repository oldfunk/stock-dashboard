#!/usr/bin/env bash
# 安装/核对母项目合并树（:8081）的 AI 交易员 cron（2026-11-19 立）。
#
# 为什么有这个脚本：这段运行时配置过去只存在于 nec 的 crontab 里。
# 2026-10-23 之后三条交易任务在一次 crontab 重写中丢失，面板照常 200、
# 计划与交易却静默停摆三周（agent_plans 最后一条 10-26 一直 pending，
# 最后一笔成交停在 10-08）。规则写进仓库，下次被误删可以一条命令核对回来。
#
# 三条交易任务各管一段（窗口由子项目代码写死，见 paper_trading/AGENTS.md）：
#   09:30-10:00  开盘执行窗口：只消费当天 pending 计划，无计划静默
#   16:30-18:30  收盘计划窗口：数据定稿到今天才做下一交易日计划
#   周六 10:00   交易日历同步（母库 trading_calendar 优先）
# 另两条 raw 收盘拉取（A1，2026-11-21）只为 NAV：15:50 给 16:30 计划窗口用，
# 20:40 源晚发布时兜底（脚本 scripts/fetch_mother_raw.sh）。
#
# 用法：
#   bash scripts/install_mother_agent_cron.sh          # 缺哪条补哪条（幂等）
#   bash scripts/install_mother_agent_cron.sh --check  # 只核对；缺则 exit 1
#
# 只在 Linux 生产机使用；Windows 开发机没有 crontab，脚本会直接退出。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PT="$ROOT/paper_trading"
PY="${PAPER_PY:-$ROOT/.venv/bin/python}"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

if ! command -v crontab >/dev/null 2>&1; then
    echo "!! 没有 crontab：本脚本只在 Linux 生产机使用"
    exit 2
fi
if [ ! -x "$PY" ]; then
    echo "!! 找不到 python：$PY（用 PAPER_PY=/path/to/python 指定）"
    exit 2
fi
mkdir -p "$PT/logs" "$ROOT/logs"

TICK_CMD="cd $PT && $PY -m paper_trading.cli --scheme mother agent tick --pool-from screening --pool-limit 15 --json >> logs/agent.log 2>&1"
CAL_CMD="cd $PT && $PY -m paper_trading.cli calendar sync --json >> logs/calendar.log 2>&1"
RAW_CMD="bash $ROOT/scripts/fetch_mother_raw.sh"
NAV_CMD="cd $ROOT && $PY scripts/nav_close_mother.py >> logs/nav_close.log 2>&1"
BENCH_CMD="cd $ROOT && $PY scripts/refresh_benchmarks.py >> logs/benchmarks.log 2>&1"
LINES=(
    "30,35,40,45,50,55 9 * * 1-5 $TICK_CMD"
    "*/5 16-18 * * 1-5 $TICK_CMD"
    "0 10 * * 6 $CAL_CMD"
    "50 15 * * 1-5 $RAW_CMD"
    "40 20 * * 1-5 $RAW_CMD"
    "50 20 * * 1-5 $NAV_CMD"
    "45 20 * * 1-5 $BENCH_CMD"
)
# 识别键：只认能唯一定位该任务的片段，避免脚本路径变化后重复安装
KEYS=("scheme mother agent tick" "scheme mother agent tick"
      "calendar sync --json >> logs/calendar.log"
      "50 15 * * 1-5 bash" "40 20 * * 1-5 bash"
      "nav_close_mother.py" "refresh_benchmarks.py")

cur="$(crontab -l 2>/dev/null || true)"
missing=()
for i in "${!LINES[@]}"; do
    if ! printf '%s\n' "$cur" | grep -Fq "${KEYS[$i]}"; then
        missing+=("${LINES[$i]}")
    fi
done

if [ "${#missing[@]}" -eq 0 ]; then
    echo "✓ 母侧 AI 交易员 cron 七条齐全"
    exit 0
fi
if [ "$CHECK" = "1" ]; then
    echo "!! 缺少 ${#missing[@]} 条（共 7 条）"
    printf '   %s\n' ${missing[@]+"${missing[@]}"}
    exit 1
fi

BACKUP="$HOME/crontab-backup-$(date +%Y%m%d-%H%M%S).txt"
printf '%s\n' "$cur" > "$BACKUP"
{ [ -n "$cur" ] && printf '%s\n' "$cur"
  printf '%s\n' ${missing[@]+"${missing[@]}"}; } | crontab -
echo "✓ 已补 ${#missing[@]} 条（原 crontab 备份：$BACKUP）"
crontab -l | grep -F "scheme mother agent tick" | sed 's/^/   /'
