"""
股票看板 - 后台实时调度器

在 Web 服务进程中运行，负责：
1. 盘中定时轮询大盘指数（30分钟）
2. 盘中定时轮询选股池实时行情（5分钟）
3. 每日 15:30 自动触发选股流水线

所有实时数据通过 /api/realtime 接口推送给前端。

行情抓取与解析统一复用 src.utils 与 src.collector.akshare_fetcher，
本模块只保留「调度」与「选股池实时行情」相关逻辑。
"""

import logging
import threading
import time
from datetime import time as dtime, datetime
from typing import Optional

from src.utils import now_cn, curl_get, tc_encode, parse_tc_line, split_tc_response

logger = logging.getLogger(__name__)

# ── 实时价格缓存（线程安全） ──
_realtime_cache: dict = {}
_cache_lock = threading.Lock()

# ── 活跃选股代码（由 routes 在启动时注入） ──
_active_codes: list[str] = []


def set_active_codes(codes: list[str]):
    global _active_codes
    _active_codes = codes


def get_realtime_cache() -> dict:
    """获取实时行情快照（线程安全）"""
    with _cache_lock:
        return dict(_realtime_cache)


# ── 工具函数 ──

def _is_trading_time() -> bool:
    """A股交易时间判断：周一至周五 9:30-11:30, 13:00-15:00（Asia/Shanghai）"""
    now = now_cn()
    if now.weekday() >= 5:  # 周六日
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30) or
            dtime(13, 0) <= t <= dtime(15, 0))


# ── 数据采集任务 ──

def fetch_stock_realtime(codes: list[str]) -> dict[str, dict]:
    """批量获取股票实时行情（腾讯 qt.gtimg.cn）"""
    if not codes:
        return {}
    tc_codes = [tc_encode(c) for c in codes]
    batch_size = 80
    results: dict[str, dict] = {}
    for i in range(0, len(tc_codes), batch_size):
        batch = tc_codes[i:i + batch_size]
        raw = curl_get(f"https://qt.gtimg.cn/q={','.join(batch)}", timeout=20)
        if raw:
            for val in split_tc_response(raw):
                q = parse_tc_line(val)
                if q:
                    results[q['code']] = q
        if i + batch_size < len(tc_codes):
            time.sleep(0.3)
    return results


# ── 调度器 ──

class MarketScheduler:
    """后台行情调度器，作为 daemon 线程运行"""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 配置
        self.index_interval = 30 * 60       # 大盘轮询间隔（秒）
        self.stock_interval = 5 * 60        # 选股池轮询间隔（秒）
        self.daily_time = dtime(15, 30)     # 每日自动运行时间
        # 初始化为当前时间，配合 start() 中的首次立即轮询，避免后台线程启动时重复触发
        self._last_index_time = time.time()
        self._last_stock_time = time.time()
        # 启动时从数据库检查今日是否已完成流水线，避免重启后重复触发
        self._last_daily_date = self._get_last_completed_date()
        # 周六复盘状态（避免重启后重复触发）
        self._last_review_date = self._get_last_review_date()
        # 复盘进行中标志：防止并发/30s 洪水式重复触发
        self._review_in_progress = False

    def _get_last_completed_date(self):
        """从数据库获取最新完成运行的日期，用于避免重启后重复触发当日流水线"""
        try:
            from src.models.database import RunLogDAO
            run_id = RunLogDAO().get_latest_completed_run_id()
            if run_id:
                # run_id 格式：YYYYMMDD_HHMMSS
                date_str = run_id.split('_')[0]
                return datetime.strptime(date_str, "%Y%m%d").date()
        except Exception:
            pass
        return None

    def _get_last_review_date(self):
        """从 ai_journal 表获取最新复盘日期，避免重启后重复触发"""
        try:
            from src.models.ai_watchlist import AiJournalDAO
            latest = AiJournalDAO().get_latest()
            if latest:
                return datetime.strptime(
                    latest['journal_date'], "%Y-%m-%d"
                ).date()
        except Exception:
            pass
        return None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # 首次启动时立即从数据库加载选股代码并抓行情
        global _active_codes
        if not _active_codes:
            try:
                from src.models.database import ScreeningResultDAO
                stocks = ScreeningResultDAO().get_latest_results()
                _active_codes = [s['code'] for s in stocks]
            except Exception:
                pass
        self._poll_indices()
        self._last_index_time = time.time()
        self._poll_stocks(force=True)
        self._last_stock_time = time.time()
        logger.info("[调度器] 已启动")

    def stop(self):
        self._stop.set()
        logger.info("[调度器] 已停止")

    def _run(self):
        while not self._stop.is_set():
            try:
                now = time.time()
                # 大盘指数（不限交易时间）
                if now - self._last_index_time >= self.index_interval:
                    self._poll_indices()
                    self._last_index_time = now

                # 选股池实时行情（仅交易时间）
                if now - self._last_stock_time >= self.stock_interval:
                    if _is_trading_time():
                        self._poll_stocks()
                    self._last_stock_time = now

                # 每日收盘流水线（交易日 15:30 后触发一次）
                self._check_daily_pipeline()

                # 每周六 00:00 触发 AI 观察池复盘
                self._check_weekly_review()

            except Exception as e:
                logger.warning(f"[调度器] 运行异常: {e}")
            # 每 30 秒检查一次停止标志
            self._stop.wait(30)

    def _poll_indices(self):
        """轮询大盘指数并存入数据库（委托 collector 统一实现）"""
        from src.models.database import MarketIndexDAO
        from src.collector.akshare_fetcher import fetch_market_index
        try:
            indices = fetch_market_index()
            if indices:
                MarketIndexDAO().save(indices)
                names = [i['index_name'] for i in indices]
                logger.info(f"[调度器] 大盘更新: {', '.join(names)}")
        except Exception as e:
            logger.warning(f"[调度器] 大盘轮询失败: {e}")

    def _poll_stocks(self, force=False):
        """轮询选股池实时行情并更新缓存"""
        global _realtime_cache, _active_codes
        codes = _active_codes
        if not codes:
            return
        # 非强制模式下，非交易时间跳过
        if not force and not _is_trading_time():
            return
        try:
            quotes = fetch_stock_realtime(codes)
            if quotes:
                with _cache_lock:
                    _realtime_cache.update(quotes)
                logger.info(
                    f"[调度器] 实时行情更新: {len(quotes)} 只")
        except Exception as e:
            logger.warning(f"[调度器] 行情轮询失败: {e}")

    def _check_daily_pipeline(self):
        """检查是否需要触发每日流水线"""
        now = now_cn()
        today = now.date()
        # 交易日 + 时间超过 15:30 + 当天未运行过
        if now.weekday() >= 5:
            return
        if now.time() < self.daily_time:
            return
        if self._last_daily_date == today:
            return

        logger.info("[调度器] 触发每日选股流水线...")
        try:
            from src.orchestrator import run_daily_pipeline
            from src.config import load_config
            config = load_config()
            run_daily_pipeline(config)
            self._last_daily_date = today
            logger.info("[调度器] 每日流水线完成")

            # 流水线后追加 K 线数据拉取
            self._fetch_kline_daily()

            # AI 分析改由外部 AI 执行（读 API/库 → 写笔记），面板不再本地触发
            # （2026-09-21 方向；本地 Zen/备用通道已确认不可用，触发只会空转失败）
            # self._trigger_ai_analysis_async(config)
        except Exception as e:
            logger.warning(f"[调度器] 每日流水线失败: {e}")

    def _fetch_kline_daily(self):
        """每日拉取观察池+候选股的K线数据（增量更新）"""
        from src.models.database import KlineDAO
        from src.collector.akshare_fetcher import fetch_kline_data
        from src.models.ai_watchlist import AiWatchlistDAO
        from src.models.database import ScreeningResultDAO

        # 合并需要拉取的代码：观察池 + 候选股 Top25
        watchlist = AiWatchlistDAO().get_all()
        codes = {w['code'] for w in watchlist}
        try:
            candidates = ScreeningResultDAO().get_latest_results(limit=25)
            codes.update(c['code'] for c in candidates)
        except Exception:
            pass

        if not codes:
            logger.info("[调度器] K线拉取：无待拉取股票")
            return

        dao = KlineDAO()
        success = 0
        for code in codes:
            latest = dao.get_latest_date(code)
            records = fetch_kline_data(code, start_date=latest)
            if records:
                dao.upsert_many(code, records)
                success += 1
        logger.info(f"[调度器] K线拉取完成: {success}/{len(codes)} 只成功")

    def _check_weekly_review(self):
        """检查是否需要触发周六 AI 复盘

        周六任意时刻进程还活着且当天没跑过就触发一次。
        _last_review_date 在 _run_review 成功后才设置，
        避免失败重试时被错误跳过。
        """
        now = now_cn()
        if now.weekday() != 5:  # 周六
            return
        if self._last_review_date == now.date():
            return
        if self._review_in_progress:
            logger.info("[复盘] 上次复盘仍在进行，跳过本次触发")
            return
        self._review_in_progress = True
        threading.Thread(target=self._run_review, daemon=True).start()

    def _run_review(self):
        """后台跑复盘，不阻塞主调度循环"""
        try:
            from src.analyzer.watchlist_reviewer import WatchlistReviewer
            from src.models.database import RunLogDAO
            from src.config import load_config

            config = load_config()
            ai_review_cfg = config.get("ai_review", {})
            if not ai_review_cfg.get("enabled", True):
                logger.info("[复盘] ai_review.enabled=False, 跳过")
                return

            run_id = now_cn().strftime("%Y%m%d_%H%M%S") + "_review"
            RunLogDAO().start_run(run_id)
            logger.info(f"[复盘] 启动周六复盘 run_id={run_id}")

            reviewer = WatchlistReviewer(config)
            result = reviewer.review(run_id)

            if result.get("skipped"):
                logger.info(f"[复盘] 跳过: {result.get('reason')}")
                RunLogDAO().complete_run(run_id, 0, 0, 0)
            else:
                new_count = len(result.get("new_watchlist", []))
                RunLogDAO().complete_run(run_id, 0, new_count, 0)
                logger.info(f"[复盘] 完成，观察池 {new_count} 只")

            # 只在成功后才标记当日已完成
            self._last_review_date = now_cn().date()
        except Exception as e:
            logger.warning(f"[复盘] 异常: {e}", exc_info=True)
            # 失败也标记当日已完成，避免 30s 洪水式重试
            self._last_review_date = now_cn().date()
            try:
                RunLogDAO().complete_run(
                    run_id, 0, 0, 0, f"error: {e}"
                )
            except Exception:
                pass
        finally:
            self._review_in_progress = False

    def _trigger_ai_analysis_async(self, config: dict):
        """在后台线程中异步触发 AI 分析（全量模式）"""
        import threading
        
        def _run_ai_analysis():
            try:
                from src.analyzer.ai_analyzer import analyze_batch
                from src.models.database import (
                    ScreeningResultDAO, RunLogDAO, PipelineProgressDAO
                )
                
                # 获取最新完成的 run_id
                run_id = RunLogDAO().get_latest_completed_run_id()
                if not run_id:
                    logger.warning("[AI分析] 无完成的流水线，跳过")
                    return
                
                stocks = ScreeningResultDAO().get_results_for_run(run_id)
                if not stocks:
                    logger.warning("[AI分析] 无筛选结果，跳过")
                    return
                
                # 富集财务历史数据（同 run_ai_analysis.py）
                from src.models.database import FinancialSummaryDAO
                fs_dao = FinancialSummaryDAO()
                for s in stocks:
                    fs = fs_dao.get(s['code'])
                    if fs:
                        for k, v in fs.items():
                            if k not in ('stock_code', 'updated_at') and v is not None:
                                s[k] = v
                
                logger.info(f"[调度器] 启动 AI 分析 {len(stocks)} 只股票...")
                
                # 进度回调：更新 pipeline_progress
                def on_progress(ai_done: int, ai_failed: int, idx: int):
                    PipelineProgressDAO().update(
                        run_id, 
                        ai_done=ai_done, 
                        ai_failed=ai_failed
                    )
                
                analyzed_ok, analyzed_failed = analyze_batch(
                    stocks, run_id, interval_seconds=60, on_progress=on_progress
                )
                logger.info(f"[调度器] AI 分析完成: 成功 {analyzed_ok}/{len(stocks)}（失败 {analyzed_failed}）")
                
                # 发送Discord通知（如果配置了webhook）
                if analyzed_failed > 0:
                    from src.notifications.discord_notifier import get_discord_notifier
                    notifier = get_discord_notifier()
                    if notifier:
                        # 获取失败原因
                        from src.models.database import ScreeningResultDAO
                        failed_stocks = ScreeningResultDAO().get_results_for_run(run_id, limit=100)
                        failed_reasons = []
                        for stock in failed_stocks:
                            if stock.get('ai_failed'):
                                reason = stock.get('ai_failure_reason', '未知原因')
                                failed_reasons.append(f"{stock['name']}({stock['code']}): {reason}")
                        
                        notifier.send_ai_failure_notification(
                            run_id=run_id,
                            failed_count=analyzed_failed,
                            failed_reasons=failed_reasons,
                            total_count=len(stocks)
                        )
                
                # 回写本次分析的 run_log.analyzed_count，保持运行记录完整
                RunLogDAO().update_analyzed_count(run_id, analyzed_ok)
            except Exception as e:
                logger.warning(f"[调度器] AI 分析异常: {e}")
        
        # 后台线程执行，不阻塞主调度循环
        t = threading.Thread(target=_run_ai_analysis, daemon=True)
        t.start()
        logger.info("[调度器] AI 分析已在后台启动")
