"""
每日选股主流程编排

职责：采集 → 初筛 → 财务补充 → 价值筛选，并把结果写入 screening_result。
AI 分析由 cron 单独触发（scripts/run_ai_analysis.py），不在此处执行。

并发保护：通过模块级 Lock 保证同一时刻只有一个流水线在跑
（调度器 15:30 触发 与 Web /api/trigger_update 手动触发 可能并发）。

对外暴露：
- run_collect_and_screen(config) → (top_stocks, run_id, run_date, total_stocks)
  采集+筛选的原子单元，scripts/run_pipeline.py 复用以避免逻辑重复。
- run_daily_pipeline(config) → 上面函数的带锁封装，含 ROE 质量门禁。
"""

import logging
import threading

from src.config import load_config as _load_config
from src.models.database import (
    init_database, RunLogDAO, PipelineProgressDAO, StockSnapshotDAO, db_conn,
)
from src.utils import now_cn

logger = logging.getLogger(__name__)

# 流水线并发锁：防止调度器与手动触发同时跑
_pipeline_lock = threading.Lock()


def load_config() -> dict:
    """加载配置（转发到 src.config，保持向后兼容）"""
    return _load_config()


def run_collect_and_screen(config: dict) -> tuple[list[dict], str, str, int]:
    """采集 + 初筛 + 财务补充 + 价值筛选。

    返回 (top_stocks, run_id, run_date, total_stocks)。
    top_stocks 为空表示流水线失败/无候选。
    """
    from src.collector.akshare_fetcher import run_collect_pipeline
    from src.screener.value_screener import run_screener

    run_id = now_cn().strftime("%Y%m%d_%H%M%S")
    run_date = now_cn().strftime("%Y-%m-%d")
    progress = PipelineProgressDAO()

    RunLogDAO().start_run(run_id)
    logger.info("\n[1/3] Data collection pipeline")
    progress.init_run(run_id, 'collecting', '采集全A股...', total=0, ai_total=0)

    candidates = run_collect_pipeline(config)
    total_stocks = StockSnapshotDAO().count()
    progress.update(run_id, 'collecting',
                    f'采集完成 {total_stocks} 只',
                    processed=total_stocks, total=total_stocks)

    if not candidates:
        logger.warning("No candidates from pipeline")
        RunLogDAO().complete_run(run_id, total_stocks, 0, 0, "no candidates")
        progress.update(run_id, 'done', '无候选股')
        return [], run_id, run_date, total_stocks

    # ── 历史财务数据采集（本地数据仓库）──
    logger.info("\n[财务历史] 拉取历史财务数据...")
    progress.update(run_id, 'history', '拉取历史财务数据...')
    try:
        from datetime import datetime
        from src.collector.akshare_fetcher import (
            collect_historical_financial_data, rebuild_financial_summaries
        )
        # 周六全量刷新，平日仅补充缺失或报告期过旧的股票
        is_weekend_full = datetime.now().weekday() == 5
        history_count = collect_historical_financial_data(
            candidates, force_full=is_weekend_full
        )
        # 无论是否有新增数据，都重建汇总以反映最新财报
        rebuild_financial_summaries(candidates)
    except Exception as e:
        logger.warning(f"[财务历史] 采集异常（不影响主流程）: {e}")

    enriched = sum(1 for c in candidates if c.get('roe') is not None)
    logger.info(f"  Candidates: {len(candidates)}, with ROE data: {enriched}")

    # ── 质量门禁 ──
    # ROE 覆盖率 < 50% 说明财务补充失败，中止本次管道，保留上一次结果不变
    if enriched < len(candidates) * 0.5:
        logger.error(
            f"[门禁] ROE 覆盖率 {enriched}/{len(candidates)} < 50%，管道中止")
        RunLogDAO().complete_run(
            run_id, total_stocks, 0, 0,
            f"ROE coverage {enriched}/{len(candidates)} < 50%, aborted")
        progress.update(run_id, 'done', 'ROE 覆盖率不足，中止')
        return [], run_id, run_date, total_stocks

    logger.info("\n[2/3] Value screening")
    progress.update(run_id, 'screening', f'价值筛选 {len(candidates)} 只...')
    top_stocks = run_screener(config, candidates, run_id, run_date)
    progress.update(run_id, 'screened',
                    f'筛选完成 {len(top_stocks)} 只', ai_total=len(top_stocks))

    if top_stocks:
        logger.info(f"  Selected: {len(top_stocks)} stocks")
        for s in top_stocks:
            logger.info(
                f"  {s['code']} {s['name']:10s} score={s['score']:.0f}  "
                f"PE={s['pe']} PB={s.get('pb')} ROE={s.get('roe')}%")

    return top_stocks, run_id, run_date, total_stocks


def run_daily_pipeline(config: dict = None) -> bool:
    """执行每日选股流水线（带并发锁）。

    返回 True 表示成功执行完整流水线，False 表示因锁竞争跳过。
    AI 分析由 cron 单独触发，不在此处执行。
    """
    if config is None:
        config = _load_config()

    # 非阻塞抢锁：若已有流水线在跑，直接跳过避免并发写入
    if not _pipeline_lock.acquire(blocking=False):
        logger.warning("[流水线] 已有流水线正在运行，跳过本次触发")
        return False

    try:
        init_database()
        logger.info("=" * 55)
        logger.info("Stock Selection Pipeline")
        logger.info(now_cn().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("=" * 55)

        top_stocks, run_id, run_date, total_stocks = run_collect_and_screen(config)

        if not top_stocks:
            logger.warning("Pipeline produced no stocks")
            return True  # 流程本身跑完，只是无结果

        RunLogDAO().complete_run(run_id, total_stocks, len(top_stocks), 0)

        # 清理过期大盘数据（保留 30 天 + 每日每指数最后一条），避免 market_index 无限增长
        try:
            from src.models.database import MarketIndexDAO
            deleted = MarketIndexDAO().cleanup_old(keep_days=30)
            if deleted:
                logger.info(f"[清理] market_index 删除 {deleted} 条过期记录（>30天）")
        except Exception as e:
            logger.warning(f"[清理] market_index 清理失败（不影响主流程）: {e}")

        # 更新 watchlist 钉选股票的行情（从腾讯实时接口拉一次）
        try:
            from src.models.database import WatchlistDAO
            from src.scheduler import fetch_stock_realtime
            watch_codes = list(WatchlistDAO().get_watched_codes())
            if watch_codes:
                quotes = fetch_stock_realtime(watch_codes)
                if quotes:
                    wdao = WatchlistDAO()
                    for code, q in quotes.items():
                        if q.get('current_price') is not None:
                            wdao.update_price(code, q['current_price'])
                    logger.info(f"[Watchlist] 更新 {len(quotes)} 只钉选股票行情")
        except Exception as e:
            logger.warning(f"[Watchlist] 行情更新失败（不影响主流程）: {e}")

        logger.info(f"\n{'=' * 55}")
        logger.info("Pipeline complete")
        logger.info(f"  Market: {total_stocks} stocks")
        logger.info(f"  Selected: {len(top_stocks)}")
        logger.info(f"  AI analyzed: via background cron (scripts/run_ai_analysis.py)")
        logger.info(f"{'=' * 55}")
        return True
    except Exception as e:
        logger.error(f"[流水线] 异常: {e}", exc_info=True)
        try:
            # 尽力标记当前 run 失败：run_id 可能已生成，用 run_log 最新一条 running 兜底
            with db_conn() as conn:
                row = conn.execute(
                    "SELECT run_id FROM run_log WHERE status='running' "
                    "ORDER BY start_time DESC LIMIT 1"
                ).fetchone()
                if row:
                    RunLogDAO().complete_run(row['run_id'], 0, 0, 0, str(e))
        except Exception:
            pass
        return False
    finally:
        _pipeline_lock.release()


def main():
    config = _load_config()
    run_daily_pipeline(config)


if __name__ == "__main__":
    main()
