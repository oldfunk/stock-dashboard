#!/usr/bin/env bash
# AI分析：全量模式，每次跑全部待分析股票
# 配合凌晨2-4点 cron 使用，离峰时段成功率更高
cd /home/debian/stock-dashboard
exec /home/debian/stock-dashboard/.venv/bin/python scripts/run_ai_analysis.py --all
