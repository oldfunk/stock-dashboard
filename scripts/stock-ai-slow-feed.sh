#!/usr/bin/env bash
# 每日选股流水线 + AI 分析（全量模式）
# 配合凌晨 cron 使用：先跑筛选，再跑 AI 分析
#
# 用法：
#   0 2 * * * /home/debian/stock-dashboard/scripts/stock-ai-slow-feed.sh
set -e
cd "$(dirname "$0")/.."

# 激活 venv（若存在）
if [ -d .venv ]; then
    source .venv/bin/activate
fi

echo "[$(date)] === 开始每日选股流水线 ==="
python3 scripts/run_pipeline.py

echo "[$(date)] === 开始 AI 分析（全量模式）==="
python3 scripts/run_ai_analysis.py --all

echo "[$(date)] === 完成 ==="
