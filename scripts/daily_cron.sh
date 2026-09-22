#!/usr/bin/env bash
# OS crontab 兜底脚本 — 如果 Web 服务挂了，cron 也能跑完整流水线
# 安装：crontab -e
# 30 15 * * 1-5 <部署目录>/scripts/daily_cron.sh >> /var/log/stock-dashboard-cron.log 2>&1
# 0 16 * * 5 <部署目录>/scripts/ai_analysis_cron.sh >> /var/log/stock-dashboard-ai.log 2>&1
# 陷阱：cron 只在时间点触发，机器 15:30 处于休眠/关机则当天任务直接错过、不会补跑；
# 醒来后手动执行一次 <部署目录>/scripts/daily_cron.sh 补跑。

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