#!/usr/bin/env bash
# OS crontab 兜底脚本 — 如果 Web 服务挂了，cron 也能跑完整流水线
# 安装：crontab -e
# 30 15 * * 1-5 /home/debian/stock-dashboard/scripts/daily_cron.sh >> /var/log/stock-dashboard-cron.log 2>&1
# 0 16 * * 1-5 /home/debian/stock-dashboard/scripts/ai_analysis_cron.sh >> /var/log/stock-dashboard-ai.log 2>&1

set -e
cd "$(dirname "$0")/.."

# 激活 venv
if [ -d .venv ]; then
    source .venv/bin/activate
fi

echo "========================================="
echo "[$(date)] === OS Cron: 每日流水线 + AI 分析 ==="
echo "========================================="

# 1. 跑完整流水线（采集 + 筛选）
python -m src.orchestrator 2>&1

# 2. AI 分析全量（等流水线结束后）
python scripts/run_ai_analysis.py --all 2>&1

echo "[$(date)] === OS Cron 完成 ==="