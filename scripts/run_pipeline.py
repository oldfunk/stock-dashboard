#!/usr/bin/env python3
"""运行完整流水线：采集 → 筛选 → AI 分析（顺序执行）。

数据采集与筛选复用 src.orchestrator 的实现，AI 分析复用
src.analyzer.ai_analyzer 的实现，本脚本只负责编排与进度展示。
"""
import logging
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

# 加载 .env（AI API Key 等）
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJ, '.env'), override=True)

# 统一日志配置（必须在其他模块导入前执行）
from src.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from src.config import load_config
from src.models.database import (
    init_database, RunLogDAO, PipelineProgressDAO,
)
from src.orchestrator import run_collect_and_screen
from src.analyzer.ai_analyzer import analyze_batch
from src.collector.akshare_fetcher import fetch_market_index
from src.models.database import MarketIndexDAO

init_database()
cfg = load_config()
progress = PipelineProgressDAO()

# ── 1-4. 采集 + 初筛 + 财务补充 + 价值筛选（复用 orchestrator）──
top_stocks, run_id, run_date, total = run_collect_and_screen(cfg)
n_top = len(top_stocks)
logger.info(f'筛选: {n_top} 只')
for s in top_stocks[:10]:
    logger.info(f'  score={s["score"]} {s["code"]} {s["name"]:10s} '
                f'PE={s.get("pe")} ROE={s.get("roe")}%')

# ── 4.5 估值验算闸 B1（Decimal 独立验算，只告警不阻断）──
try:
    from scripts.verify_valuation import verify_run as _verify_run
    _vr = _verify_run(run_id)
    logger.info(f'验算闸: {run_id} 通过={_vr["pass"]} '
                f'告警={_vr["warn"]} 失败={_vr["fail"]} 跳过={_vr["skip"]}')
except Exception as vr_err:
    logger.warning(f'验算闸异常（不影响后续）: {vr_err}')

# ── 5. 大盘指数（统一走腾讯源，与调度器一致）──
try:
    indices = fetch_market_index()
    if indices:
        MarketIndexDAO().save(indices)
        for i in indices:
            logger.info(f'  大盘: {i["index_name"]} '
                        f'{i["current_value"]} ({i["change_percent"]:+.2f}%)')
    else:
        logger.warning('大盘指数获取为空（不影响后续）')
except Exception as idx_err:
    logger.warning(f'大盘指数获取失败（不影响后续）: {idx_err}')

# ── 6. AI 分析（复用 ai_analyzer，顺序执行）──
if not top_stocks:
    logger.warning('无入选股票，跳过 AI 分析')
    RunLogDAO().complete_run(run_id, total, n_top, 0)
    progress.update(run_id, 'done', '无股票可分析')
    sys.exit(0)

progress.update(run_id, 'analyzing', f'AI分析 0/{n_top}...',
                ai_total=n_top, ai_done=0, ai_failed=0)
logger.info(f'开始 AI 分析 {n_top} 只...')


def _on_progress(ok: int, failed: int, idx: int):
    progress.update(run_id, stage_label=f'AI分析 {idx}/{n_top}...',
                    ai_done=ok, ai_failed=failed)


analyzed_ok, analyzed_failed = analyze_batch(
    top_stocks, run_id, interval_seconds=60, on_progress=_on_progress)

# ── 完成 ──
RunLogDAO().complete_run(run_id, total, n_top, analyzed_ok)
progress.update(run_id, 'done',
                f'完成: 筛选{n_top}只, AI分析{analyzed_ok}只')
logger.info(f'流水线完成: 全A股{total}只 → 筛选{n_top}只 → '
            f'AI分析{analyzed_ok}只（失败{analyzed_failed}只）')
