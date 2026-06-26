"""
每日选股主流程编排
使用采集管的 run_collect_pipeline → 筛选 → AI 分析
"""

import logging
import os
import sys
import yaml
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    local_path = Path(__file__).parent.parent / "config" / "local.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            _deep_merge(config, yaml.safe_load(f))
    return config


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def run_daily_pipeline(config: dict = None):
    from src.models.database import init_database, ScreeningResultDAO, RunLogDAO

    if config is None:
        config = load_config()

    init_database()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_date = datetime.now().strftime("%Y-%m-%d")

    logger.info("=" * 55)
    logger.info("Stock Selection Pipeline")
    logger.info(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 55)

    # Step 1-4: Collect pipeline (indices → snapshot → pre-filter → financial)
    from src.collector.akshare_fetcher import run_collect_pipeline

    RunLogDAO().start_run(run_id)
    logger.info("\n[1/3] Data collection pipeline")
    candidates = run_collect_pipeline(config)

    if not candidates:
        logger.warning("No candidates from pipeline")
        RunLogDAO().complete_run(run_id, 0, 0, 0, "no candidates")
        return

    total_stocks = 0
    from src.models.database import get_connection
    conn = get_connection()
    r = conn.execute("SELECT COUNT(*) FROM stock_snapshot").fetchone()
    total_stocks = r[0] if r else 0
    conn.close()

    enriched = sum(1 for c in candidates if c.get('roe') is not None)
    logger.info(f"  Candidates: {len(candidates)}, with ROE data: {enriched}")

    # Step 5: Full screening
    logger.info("\n[2/3] Value screening")
    from src.screener.value_screener import run_screener

    top_stocks = run_screener(config, candidates, run_id, run_date)

    if not top_stocks:
        logger.warning("No stocks passed screening")
        RunLogDAO().complete_run(run_id, total_stocks, 0, 0)
        return

    logger.info(f"  Selected: {len(top_stocks)} stocks")
    for s in top_stocks:
        logger.info(f"  {s['code']} {s['name']:10s} score={s['score']:.0f}  PE={s['pe']} PB={s.get('pb')} ROE={s.get('roe')}%")

    # Step 6: AI analysis
    logger.info("\n[3/3] AI analysis")
    from src.analyzer.ai_analyzer import run_ai_analysis

    enhanced = run_ai_analysis(config, top_stocks)
    analyzed = sum(1 for s in enhanced if s.get('ai_analysis'))

    # Finish
    RunLogDAO().complete_run(run_id, total_stocks, len(top_stocks), analyzed)

    logger.info(f"\n{'=' * 55}")
    logger.info("Pipeline complete")
    logger.info(f"  Market: {total_stocks} stocks")
    logger.info(f"  Candidates: {len(candidates)}")
    logger.info(f"  Selected: {len(top_stocks)}")
    logger.info(f"  AI analyzed: {analyzed}")
    logger.info(f"{'=' * 55}")


def main():
    config = load_config()
    run_daily_pipeline(config)


if __name__ == "__main__":
    main()
