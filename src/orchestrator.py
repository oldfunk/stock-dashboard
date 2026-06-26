"""
股票盯盘看板 - 每日选股主流程编排
数据采集 → 量化筛选 → AI 分析 → 入库
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
    """加载配置（默认 + local 覆盖）"""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    local_path = Path(__file__).parent.parent / "config" / "local.yaml"

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            local_config = yaml.safe_load(f)
        # 深度合并
        _deep_merge(config, local_config)
        logger.info("[配置] 已加载 local.yaml 覆盖")

    return config


def _deep_merge(base: dict, override: dict):
    """递归合并字典"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def run_daily_pipeline(config: dict = None):
    """
    每日选股主流程：
    1. 初始化数据库
    2. 采集大盘指数
    3. 采集全A股快照
    4. 量化筛选
    5. AI 分析
    """
    if config is None:
        config = load_config()

    from src.models.database import init_database

    # 1. 初始化数据库
    init_database()
    logger.info("=" * 50)
    logger.info("📊 股票盯盘看板 - 每日选股流程")
    logger.info(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

    # 2. 采集大盘指数
    logger.info("\n📈 [第一步] 采集大盘指数...")
    from src.collector.akshare_fetcher import fetch_market_index, fetch_all_stocks_basic
    from src.models.database import MarketIndexDAO

    indices = fetch_market_index()
    if indices:
        MarketIndexDAO().save(indices)
        for idx in indices:
            logger.info(f"   {idx['index_name']}: {idx['current_value']} ({idx['change_percent']:+.2f}%)")

    # 3. 采集全 A 股快照
    logger.info(f"\n🏢 [第二步] 采集全A股数据...")
    records = fetch_all_stocks_basic()
    if records:
        from src.models.database import StockSnapshotDAO
        StockSnapshotDAO().save_batch(records)
        logger.info(f"   ✅ 保存 {len(records)} 只股票数据")
    else:
        logger.warning("   ⚠️ 数据采集为空，跳过后续流程")
        return

    # 4. 量化筛选
    logger.info(f"\n🔍 [第三步] 价值投资筛选...")
    from src.screener.value_screener import ValueScreener
    screener = ValueScreener(config.get('screener', {}).get('conditions', {}))
    candidates = screener.run()

    if not candidates:
        logger.info("   ℹ️ 本次未筛选出符合条件的股票")
        return

    logger.info(f"\n✅ 筛选结果: {len(candidates)} 只候选股票")
    for s in candidates:
        logger.info(f"   {s['code']} {s['name']:8s} 评分={s['score']}  "
                     f"PE={s['pe']} PB={s['pb']} ROE={s['roe']}%")

    # 5. AI 分析
    logger.info(f"\n🤖 [第四步] AI 选股分析...")
    from src.analyzer.ai_analyzer import run_ai_analysis
    enhanced = run_ai_analysis(config, candidates)

    logger.info(f"\n🎉 每日选股流程完成!")
    logger.info(f"   全市场: {len(records)} 只")
    logger.info(f"   筛选入选: {len(candidates)} 只")
    logger.info(f"   AI 分析: {sum(1 for s in enhanced if s.get('ai_analysis'))} 只")


def main():
    """命令行入口"""
    config = load_config()
    run_daily_pipeline(config)


if __name__ == "__main__":
    main()
