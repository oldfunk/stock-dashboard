#!/usr/bin/env bash
# 每日收盘后选股更新脚本
# 用法: ./scripts/daily_update.sh
# 建议通过 cron 定时执行: 30 15 * * 1-5 /home/debian/stock-dashboard/scripts/daily_update.sh

set -e
cd "$(dirname "$0")/.."

# 加载 .env（如果有）
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# 激活 venv（如果有）
if [ -d .venv ]; then
    source .venv/bin/activate
elif [ -d venv ]; then
    source venv/bin/activate
fi

echo "========================================="
echo "📊 股票价值投资选股 - 每日更新"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="

# 运行选股流程
python -m src.orchestrator 2>&1

# 记录完成
echo ""
echo "✅ 每日更新完成: $(date '+%Y-%m-%d %H:%M:%S')"
