#!/usr/bin/env bash
# AI 分析：全量模式，每次跑全部待分析股票
# 配合凌晨 2-4 点 cron 使用，离峰时段成功率更高
#
# 注意：本脚本通过相对路径定位项目根目录，请用绝对路径调用，例如：
#   30 2 * * * /home/debian/stock-dashboard/scripts/stock-ai-slow-feed.sh
set -e
cd "$(dirname "$0")/.."

# 激活 venv（若存在），否则用系统 python
if [ -d .venv ]; then
    source .venv/bin/activate
fi

exec python scripts/run_ai_analysis.py --all
