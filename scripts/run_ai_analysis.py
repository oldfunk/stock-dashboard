#!/usr/bin/env python3
"""
慢喂 AI 分析 — 按 cron 节奏跑，每次分析 1 只。

用法：
  python3 scripts/run_ai_analysis.py              # 分析最新批次中优先级最高的未分析股票
  python3 scripts/run_ai_analysis.py --once       # 同上，分析 1 只
  python3 scripts/run_ai_analysis.py --stale=3    # 分析 3 天未更新的股票（含已分析过的）
  python3 scripts/run_ai_analysis.py --all        # 一次全跑完（不推荐，可能限流）

退出码:
  0 = 成功分析了 1 只
  1 = 没有需要分析的股票（全部完成）
  2 = API 错误 / 未配置 API Key
"""
import os
import sys
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJ, '.env'), override=True)

from src.config import load_config
from src.models.database import (
    ScreeningResultDAO, StockAnalysisHistoryDAO, RunLogDAO, init_database,
)
from src.analyzer.ai_analyzer import AiAnalyzer, _save_analysis, _save_failure
from src.utils import now_cn

init_database()

# ── 解析参数 ──
analyze_all = '--all' in sys.argv
stale_days = 3
for a in sys.argv:
    if a.startswith('--stale='):
        stale_days = int(a.split('=')[1])

# ── 校验 API Key ──
analyzer = AiAnalyzer(load_config().get('ai', {}))
if not analyzer.configured:
    print("[ERROR] STOCK_AI_API_KEY not set in .env")
    sys.exit(2)
print(f"[AI] model={analyzer.model} api_base={analyzer.api_base}")

# ── 获取最新完成批次 ──
run_id = RunLogDAO().get_latest_completed_run_id()
if not run_id:
    print("[SKIP] No completed runs found")
    sys.exit(1)
print(f"Latest run: {run_id}")

# ── 获取候选池 ──
stocks = ScreeningResultDAO().get_results_for_run(run_id)
if not stocks:
    print("[SKIP] No stocks in latest run")
    sys.exit(1)

# ── 确定需要分析的股票 ──
pending = []
for s in stocks:
    code = s['code']
    has_analysis = bool((s.get('ai_analysis') or '').strip())

    if has_analysis and not analyze_all:
        latest = StockAnalysisHistoryDAO().get_latest_for_code(code)
        if latest and latest.get('ai_analysis') and latest['ai_analysis'] != '{}':
            age = (now_cn() - datetime.fromisoformat(latest['created_at'])).days
            if age < stale_days:
                continue  # 足够新鲜，跳过
        # 有分析但已过期，重新分析
    else:
        # 无分析：检查今天是否已经尝试过但失败了
        latest = StockAnalysisHistoryDAO().get_latest_for_code(code)
        if latest and latest.get('ai_analysis') == '{}':
            attempted_today = (
                now_cn() - datetime.fromisoformat(latest['created_at'])
            ).total_seconds() < 86400
            if attempted_today:
                continue  # 今天已试过且失败了，跳过去试下一只

    pending.append(s)

if not pending:
    print(f"[DONE] All {len(stocks)} stocks have recent analysis (stale={stale_days}d)")
    sys.exit(1)

# ── 决定本次分析几只 ──
targets = pending if analyze_all else pending[:1]
print(f"Pending: {len(pending)}, analyzing this run: {len(targets)}")

# ── 逐一分析（复用 AiAnalyzer）──
analyzed_ok = 0

for stock in targets:
    code, name = stock['code'], stock['name']
    print(f"\n[{code}] {name} (score={stock['score']})...")

    result = analyzer.analyze_stock(stock)
    if not result:
        print(f"  ✗ Failed")
        _save_failure(stock, run_id)
        if not analyze_all:
            sys.exit(2)
        continue

    _save_analysis(stock, result, run_id)
    print(f"  ✅ Saved to analysis history")
    analyzed_ok += 1

    # 非 --all 模式只分析 1 只就退出
    if not analyze_all:
        break

    # --all 模式间隔 60s
    if stock != targets[-1]:
        print("  Cooling 60s...")
        import time
        time.sleep(60)

# ── 输出摘要 ──
if analyze_all:
    remaining = len(pending) - analyzed_ok
    print(f"\nAnalyzed: {analyzed_ok}, remaining: {remaining}")
else:
    remaining = len(pending) - 1
    print(f"\nAnalyzed: {analyzed_ok} this run, {remaining} still pending")
    if remaining > 0 and analyzed_ok == 0 and len(pending) > 1:
        print(f"Next run will process: {pending[1]['code']}")

sys.exit(0 if analyzed_ok > 0 else 2)
