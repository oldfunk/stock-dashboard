#!/bin/bash
# 设置 stock-dashboard 定时任务
# 读取 docs/scheduled-tasks.md 配置，创建 Hermes cron 任务
#
# 运行位置：在 Hermes 所在机器上跑（作者的 Hermes 环境），不在生产服务器跑
# （生产服务器无 hermes 命令；任务通过 SSH/API 操作生产服务器）。
#
# 用法：bash scripts/setup-cron.sh

set -e

echo "=== Stock Dashboard 定时任务设置 ==="
echo ""

# 检查 hermes 命令
if ! command -v hermes &> /dev/null; then
    echo "错误：hermes 命令未找到"
    exit 1
fi

# 1. 每日巡检（工作日 15:30）
echo "创建任务：每日巡检（工作日 15:30）..."
hermes cron create \
    --name "stock-dashboard-daily-inspection" \
    --deliver local \
    "30 15 * * 1-5" \
    "执行 stock-dashboard 每日巡检。读取 docs/scheduled-tasks.md 中'每日巡检'配置，检查：1) 流水线运行状态（最近 run_log）2) 数据质量（screening_result 覆盖率、stock_snapshot 更新）3) 服务健康（systemctl is-active、API 响应）4) 异常检测。生成报告写入 docs/reports/daily-YYYYMMDD.md，末尾记录执行状态。" \
    2>&1 | tail -3

echo ""

# 2. 每周复盘（周六 16:00）
echo "创建任务：每周复盘（周六 16:00）..."
hermes cron create \
    --name "stock-dashboard-weekly-review" \
    --deliver local \
    "0 16 * * 6" \
    "执行 stock-dashboard 每周复盘。读取 docs/scheduled-tasks.md 中'每周复盘'配置，执行：1) 观察池复盘（WatchlistReviewer.review()）2) 论点漂移检测 3) 投资笔记撰写（ai_journal）4) 候选股表现分析。生成报告写入 docs/reports/weekly-YYYYMMDD.md，末尾记录执行状态。" \
    2>&1 | tail -3

echo ""

# 3. 每月分析（每月 1 号 10:00）
echo "创建任务：每月分析（每月 1 号 10:00）..."
hermes cron create \
    --name "stock-dashboard-monthly-analysis" \
    --deliver local \
    "0 10 1 * *" \
    "执行 stock-dashboard 每月分析。读取 docs/scheduled-tasks.md 中'每月分析'配置，分析：1) 候选股表现（本月筛选结果 vs 历史）2) 策略有效性（多策略命中分布）3) 数据源健康度（S1-S8 可用性）4) 系统资源使用（DB 大小、日志量）。生成报告写入 docs/reports/monthly-YYYYMM.md，末尾记录执行状态。" \
    2>&1 | tail -3

echo ""
echo "=== 定时任务设置完成 ==="
echo ""
echo "查看任务列表：hermes cron list"
echo "手动触发任务：hermes cron run <job-id>"
echo "查看任务历史：hermes cron runs <job-id>"
