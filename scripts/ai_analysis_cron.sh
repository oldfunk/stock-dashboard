#!/usr/bin/env bash
# 周度 AI 分析 cron 脚本（价值投资：周级节奏，每周五 16:00 收盘后）
# 安装：crontab -e
# 0 16 * * 5 /home/debian/stock-dashboard/scripts/ai_analysis_cron.sh >> /var/log/stock-dashboard-ai.log 2>&1

set -e
cd "$(dirname "$0")/.."

if [ -d .venv ]; then
    source .venv/bin/activate
fi

echo "[$(date)] === AI 分析全量模式 ==="
python scripts/run_ai_analysis.py --all 2>&1
echo "[$(date)] === AI 分析完成 ==="