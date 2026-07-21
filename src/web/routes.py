"""
Web 看板 - FastAPI 路由
"""

import json
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import load_config, start_config_watcher, stop_config_watcher
from src.models.database import (
    init_database,
    MarketIndexDAO,
    ScreeningResultDAO,
    StockAnalysisHistoryDAO,
    RunLogDAO,
    PipelineProgressDAO,
    FinancialHistoryDAO,
    FinancialSummaryDAO,
    WatchlistDAO,
    db_conn,
)
from src.models.ai_watchlist import (
    AiWatchlistDAO, AiWatchlistHistoryDAO, AiJournalDAO
)
from fastapi import HTTPException
from src.scheduler import (
    MarketScheduler, set_active_codes, get_realtime_cache, fetch_stock_realtime,
    _active_codes, _cache_lock, _realtime_cache,
)
from src.utils import now_cn

logger = logging.getLogger(__name__)


def _enrich_stocks(stocks: list[dict]) -> None:
    """给候选股列表补充解析后的 AI 分析字段、财务历史、分析历史（原地修改）。

    将原本在 index 和 /api/stocks 两个路由中重复的 enrichment 逻辑统一到此函数。
    """
    hist_dao = StockAnalysisHistoryDAO()
    fs_dao = FinancialSummaryDAO()
    watched_set = WatchlistDAO().get_watched_codes()
    import re

    for s in stocks:
        code = s['code']
        s['watched'] = code in watched_set

        # 2. 历史分析摘要（先查，最新一条用于回退填空）
        history = hist_dao.get_history(code, limit=5)
        latest_hist_ai = None  # 用于回退的最新历史 ai_analysis
        latest_hist_trade = None  # 用于回退的最新历史 ai_trade_strategy
        parsed_history = []
        for h in history:
            if h.get('ai_analysis') and h['ai_analysis'] not in ['{}', '']:
                try:
                    ai_obj = json.loads(h['ai_analysis'])
                    # 第一条（最新）保留原始 JSON 给回退用
                    if latest_hist_ai is None:
                        latest_hist_ai = ai_obj
                    if latest_hist_trade is None and h.get('ai_trade_strategy'):
                        try:
                            latest_hist_trade = json.loads(h['ai_trade_strategy']) if isinstance(h['ai_trade_strategy'], str) else h['ai_trade_strategy']
                        except (json.JSONDecodeError, TypeError):
                            pass
                    combined = []
                    if ai_obj.get('analysis'):
                        combined.append(ai_obj['analysis'])
                    if ai_obj.get('investment_strategy'):
                        combined.append(ai_obj['investment_strategy'])
                    if ai_obj.get('trade_strategy'):
                        if isinstance(ai_obj['trade_strategy'], dict):
                            parts = [f"{k}: {v}" for k, v in ai_obj['trade_strategy'].items()]
                            combined.append('; '.join(parts))
                        else:
                            combined.append(str(ai_obj['trade_strategy']))
                    if ai_obj.get('reverse_thinking'):
                        combined.append(f"[逆向思考] {ai_obj['reverse_thinking']}")
                    if ai_obj.get('mirror_counts'):
                        combined.append(f"[镜子测试] 转折词计数: {ai_obj['mirror_counts']}")
                    h['combined_analysis'] = '\n\n'.join(combined) if combined else '--'
                    h['hist_analysis'] = ai_obj.get('analysis', '') or ''
                    h['hist_strategy'] = ai_obj.get('investment_strategy', '') or ''
                    h['hist_risk'] = ai_obj.get('reverse_thinking', '') or ''
                    h['hist_trade'] = ai_obj.get('trade_strategy', {}) or {}
                    h['hist_mirror'] = ai_obj.get('mirror_counts', '') or ''
                except Exception:
                    h['combined_analysis'] = '--'
            else:
                h['combined_analysis'] = '--'
            parsed_history.append(h)
        s['analysis_history'] = parsed_history

        # 1. 解析 AI 分析 JSON 字符串为 dict；若当前 screening_result 无 AI 分析，回退到最新历史
        if s.get('ai_analysis') and isinstance(s['ai_analysis'], str):
            try:
                s['ai_parsed'] = json.loads(s['ai_analysis'])
            except (json.JSONDecodeError, TypeError):
                s['ai_parsed'] = latest_hist_ai
        else:
            s['ai_parsed'] = s.get('ai_analysis') or latest_hist_ai

        if s.get('ai_trade_strategy') and isinstance(s['ai_trade_strategy'], str):
            try:
                s['trade_parsed'] = json.loads(s['ai_trade_strategy'])
            except (json.JSONDecodeError, TypeError):
                s['trade_parsed'] = latest_hist_trade
        else:
            s['trade_parsed'] = s.get('ai_trade_strategy') or latest_hist_trade

        # 3. Mirror counts 传递到前端显示
        if s.get('ai_parsed') and isinstance(s['ai_parsed'], dict):
            mc = s['ai_parsed'].get('mirror_counts')
            if mc is not None:
                if isinstance(mc, str):
                    nums = re.findall(r'\d+', mc.split()[0] if ' ' in mc else mc)
                    total = sum(int(n) for n in nums) if nums else 0
                    s['mirror_counts'] = str(mc)
                    s['mirror_total'] = total

        # 4. 财务历史汇总（用于知识面板）
        try:
            fs = fs_dao.get(code)
            if fs:
                s['_summary'] = {
                    'roe_5y_avg': fs.get('roe_5y_avg'),
                    'gross_margin_5y_avg': fs.get('gross_margin_5y_avg'),
                    'net_margin_5y_avg': fs.get('net_margin_5y_avg'),
                    'ocf_latest': fs.get('ocf_latest'),
                    'ocf_positive_years': fs.get('ocf_positive_years'),
                    'ocf_5y_trend': fs.get('ocf_5y_trend'),
                    'intcov_5y_avg': fs.get('intcov_5y_avg'),
                    'fcf_5y_sum': fs.get('fcf_5y_sum'),
                    'share_dilution_5y': fs.get('share_dilution_5y'),
                    'roic_5y_avg': fs.get('roic_5y_avg'),
                    'data_years': fs.get('data_years'),
                    'roe_10y_avg': fs.get('roe_10y_avg'),
                    'net_margin_10y_avg': fs.get('net_margin_10y_avg'),
                    'intcov_10y_avg': fs.get('intcov_10y_avg'),
                    'fcf_10y_sum': fs.get('fcf_10y_sum'),
                    'share_dilution_10y': fs.get('share_dilution_10y'),
                    'fcf_positive_years_10': fs.get('fcf_positive_years_10'),
                    'roe_volatility': fs.get('roe_volatility'),
                    'roe_improvement': fs.get('roe_improvement'),
                    'roic_10y_avg': fs.get('roic_10y_avg'),
                }
        except Exception:
            pass

# 全局调度器
_scheduler = MarketScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：加载 .env / 配置日志 / 初始化 DB / 启动调度器 / 启动配置监听；停止时关调度器、停配置监听。"""
    from dotenv import load_dotenv
    from src.logging_config import setup_logging
    setup_logging()
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
        logger.info(f"[Web] 加载 .env: {env_path}")
    init_database()
    _scheduler.start()
    start_config_watcher(interval=2.0)  # 启动配置热重载
    logger.info("[Web] 服务启动完成")
    try:
        yield
    finally:
        _scheduler.stop()
        stop_config_watcher()
        logger.info("[Web] 服务关闭")


# -------- 创建 FastAPI 应用 --------
app = FastAPI(title="价值投资选股看板", lifespan=lifespan)


# -------- 模板和静态文件 --------
templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(templates_dir))

import markdown as _markdown


def _markdown_to_html(text: str) -> str:
    """Jinja2 过滤器：Markdown → HTML"""
    if not text:
        return ''
    return _markdown.markdown(text, extensions=['extra', 'codehilite'])


templates.env.filters['markdown_to_html'] = _markdown_to_html

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# -------- 路由 --------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """看板首页"""
    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')

    # 获取最新大盘数据
    market_dao = MarketIndexDAO()
    indices = market_dao.get_latest()

    # 获取最新筛选结果
    result_dao = ScreeningResultDAO()
    stocks = result_dao.get_latest_results()

    # 注入 active codes 到调度器（只在首次加载时）
    codes = [s['code'] for s in stocks]
    set_active_codes(codes)

    # 合并实时行情
    realtime = get_realtime_cache()
    for stock in stocks:
        code = stock['code']
        if code in realtime:
            rt = realtime[code]
            stock['current_price'] = rt.get('current_price')
            stock['change_percent'] = rt.get('change_percent')
            stock['change_amount'] = rt.get('change_amount')
        else:
            stock['current_price'] = None
            stock['change_percent'] = None
            stock['change_amount'] = None

    # 获取运行状态
    run_log = RunLogDAO().get_latest_run()

    # 解析 AI 分析 JSON + 历史 + 财务汇总
    _enrich_stocks(stocks)

    # 获取 AI 观察池 + 合并实时行情 + 最近 AI Signal
    ai_watchlist = AiWatchlistDAO().get_all()
    realtime = get_realtime_cache()
    hist_dao = StockAnalysisHistoryDAO()
    for item in ai_watchlist:
        code = item['code']
        if code in realtime:
            item['current_price'] = realtime[code].get('current_price')
            item['change_percent'] = realtime[code].get('change_percent')
        # 取最近 Signal
        latest_hist = hist_dao.get_latest_for_code(code)
        if latest_hist and latest_hist.get('ai_trade_strategy'):
            try:
                trade = json.loads(latest_hist['ai_trade_strategy'])
                item['signal'] = trade.get('signal')
            except (json.JSONDecodeError, TypeError):
                item['signal'] = None
        else:
            item['signal'] = None

    # 获取最新笔记摘要
    ai_journal_latest = AiJournalDAO().get_latest()

    refresh = config.get('web', {}).get('refresh_interval', 30)

    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "page_title": page_title,
        "indices": indices,
        "stocks": stocks,
        "run_log": run_log,
        "refresh_interval": refresh,
        "ai_watchlist": ai_watchlist,
        "ai_journal_latest": ai_journal_latest,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/candidates", response_class=HTMLResponse)
async def candidates(request: Request):
    """候选股总览页 — 显示完整筛选结果（不再限于 Top 20）"""
    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')

    # 获取最新筛选结果
    result_dao = ScreeningResultDAO()
    stocks = result_dao.get_latest_results()

    # 合并实时行情
    realtime = get_realtime_cache()
    for stock in stocks:
        code = stock['code']
        if code in realtime:
            rt = realtime[code]
            stock['current_price'] = rt.get('current_price')
            stock['change_percent'] = rt.get('change_percent')
            stock['change_amount'] = rt.get('change_amount')
        else:
            stock['current_price'] = None
            stock['change_percent'] = None
            stock['change_amount'] = None

    # 解析 AI 分析 JSON + 历史 + 财务汇总
    _enrich_stocks(stocks)

    return templates.TemplateResponse(request, "candidates.html", {
        "request": request,
        "page_title": page_title,
        "stocks": stocks,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/api/indices")
async def api_indices():
    """大盘数据 API"""
    indices = MarketIndexDAO().get_latest()
    return [dict(i) for i in indices]


@app.get("/api/stocks")
async def api_stocks():
    """最新选股结果 + 实时行情 API"""
    stocks = ScreeningResultDAO().get_latest_results()

    # 确保 active codes 已注册
    codes = [s['code'] for s in stocks]
    set_active_codes(codes)

    # 合并实时行情，缓存空时主动拉一次
    realtime = get_realtime_cache()
    if not realtime and codes:
        quotes = fetch_stock_realtime(codes)
        if quotes:
            with _cache_lock:
                _realtime_cache.update(quotes)
            realtime = quotes
    for s in stocks:
        code = s['code']
        if code in realtime:
            rt = realtime[code]
            s['current_price'] = rt.get('current_price')
            s['change_percent'] = rt.get('change_percent')
            s['change_amount'] = rt.get('change_amount')
        else:
            s['current_price'] = None
            s['change_percent'] = None
            s['change_amount'] = None

    # 解析 AI 分析 JSON + 历史 + 财务汇总
    _enrich_stocks(stocks)

    # 渲染 _stock_list.html 用于前端全量替换
    try:
        stocks_html = templates.get_template('_stock_list.html').render(
            stocks=stocks,
            request=None,
        )
    except Exception:
        stocks_html = ''

    run = RunLogDAO().get_latest_run()
    run_id = str(run['run_id']) if run else None

    return {
        "stocks": [dict(s) for s in stocks],
        "_html": stocks_html,
        "_run_id": run_id,
        "_count": len(stocks),
    }


@app.get("/api/history/{code}")
async def api_stock_history(code: str):
    """单只股票的历史分析记录"""
    return StockAnalysisHistoryDAO().get_history(code, limit=20)


@app.get("/api/status")
async def api_status():
    """运行状态 API"""
    run = RunLogDAO().get_latest_run()
    result = dict(run) if run else {"status": "no_runs"}
    try:
        p = PipelineProgressDAO().get_progress()
        if p:
            result['progress'] = dict(p)
    except Exception:
        pass
    return result


@app.get("/api/progress")
async def api_progress():
    """流水线进度 API"""
    try:
        p = PipelineProgressDAO().get_progress()
        return dict(p) if p else {"stage": "idle", "stage_label": "无运行中任务"}
    except Exception as e:
        return {"stage": "error", "stage_label": str(e)}


@app.get("/api/realtime")
async def api_realtime():
    """纯实时行情 API — 仅返回价格变化信息"""
    cache = get_realtime_cache()

    # 如果缓存为空，主动拉一次
    if not cache and _active_codes:
        quotes = fetch_stock_realtime(_active_codes)
        if quotes:
            cache.update(quotes)

    # 只返回前端需要的关键字段
    result = []
    for code, q in cache.items():
        result.append({
            'code': code,
            'name': q.get('name'),
            'current_price': q.get('current_price'),
            'prev_close': q.get('prev_close'),
            'change_percent': q.get('change_percent'),
            'change_amount': q.get('change_amount'),
            'high': q.get('high'),
            'low': q.get('low'),
            'volume': q.get('volume'),
            'amount': q.get('amount'),
        })
    return result


@app.get("/api/config")
async def api_config():
    """返回当前生效的配置（脱敏 API Key 等敏感字段）"""
    config = load_config()
    # 脱敏处理
    safe_config = dict(config)
    if 'ai' in safe_config:
        safe_config['ai'] = dict(safe_config['ai'])
        for key in ['api_key', 'api_base', 'model']:
            if key in safe_config['ai'] and safe_config['ai'][key]:
                val = str(safe_config['ai'][key])
                if len(val) > 8:
                    safe_config['ai'][key] = val[:4] + '****' + val[-4:]
                else:
                    safe_config['ai'][key] = '****'
    return safe_config


@app.get("/api/data-quality")
async def api_data_quality():
    """数据质量概览：最新快照日期、ROE覆盖率、财务历史覆盖年数、异常值计数"""
    from src.models.database import (
        StockSnapshotDAO, FinancialSummaryDAO, FinancialHistoryDAO
    )
    from datetime import datetime, timedelta
    
    snapshot_dao = StockSnapshotDAO()
    fs_dao = FinancialSummaryDAO()
    fh_dao = FinancialHistoryDAO()
    
    # 1. 最新快照日期
    latest_snapshot_date = snapshot_dao.get_latest_snapshot_date()
    
    # 2. ROE 覆盖率（有 ROE 数据的股票占全市场比例）
    total_stocks = snapshot_dao.count()
    # 近期快照中有 ROE 的
    roe_covered = 0
    if latest_snapshot_date:
        with db_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as cnt FROM stock_snapshot
                WHERE snapshot_date = ? AND roe IS NOT NULL AND roe > 0
            """, (latest_snapshot_date,)).fetchone()
            roe_covered = row['cnt'] if row else 0
    
    roe_coverage = round(roe_covered / total_stocks * 100, 1) if total_stocks > 0 else 0
    
    # 3. 财务历史覆盖年数（取中位数或平均）
    try:
        with db_conn() as conn:
            row = conn.execute("""
                SELECT AVG(year_count) as avg_years FROM (
                    SELECT stock_code, COUNT(DISTINCT substr(report_date, 1, 4)) as year_count
                    FROM financial_history
                    GROUP BY stock_code
                )
            """).fetchone()
            avg_hist_years = round(row['avg_years'], 1) if row and row['avg_years'] else 0
    except Exception:
        avg_hist_years = 0
    
    # 4. 异常值计数（ROE>100%、负市值、PE<0 等）
    try:
        with db_conn() as conn:
            anomalies = conn.execute("""
                SELECT COUNT(*) as cnt FROM stock_snapshot
                WHERE snapshot_date = ? AND (
                    roe > 100 OR market_cap <= 0 OR pe < 0 OR pb < 0 OR debt_ratio > 100
                )
            """, (latest_snapshot_date,)).fetchone()
            anomaly_count = anomalies['cnt'] if anomalies else 0
    except Exception:
        anomaly_count = 0
    
    return {
        "latest_snapshot_date": latest_snapshot_date,
        "total_stocks": total_stocks,
        "roe_coverage_pct": roe_coverage,
        "avg_financial_history_years": avg_hist_years,
        "anomaly_count": anomaly_count,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


@app.post("/api/trigger_update")
async def trigger_update():
    """手动触发每日更新（后台线程；orchestrator 内部有锁防并发）"""
    from src.orchestrator import run_daily_pipeline
    import threading
    config = load_config()
    thread = threading.Thread(target=run_daily_pipeline, args=(config,), daemon=True)
    thread.start()
    return {"status": "started", "message": "更新流程已启动，请在日志中查看进度"}


# ── 搜索 + 钉选 ──────────────────────────────────────────────

@app.get("/api/search")
async def api_search(q: str = ""):
    """搜索股票（code 或名称模糊匹配，搜全 A 股 stock_snapshot）。

    返回每只股票的基础行情 + 是否在最新榜单 + 最近 AI 信号 + 是否已钉选。
    """
    q = (q or "").strip()
    if not q:
        return []
    pattern = f"%{q}%"
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.code, s.name, s.pe, s.pb, s.roe, s.market_cap,
                   s.current_price, s.revenue_growth, s.profit_growth,
                   s.debt_ratio,
                   EXISTS(SELECT 1 FROM screening_result sr
                          WHERE sr.code = s.code
                          AND sr.run_id = (SELECT MAX(run_id) FROM screening_result)) as in_list,
                   (SELECT sah.ai_trade_strategy FROM stock_analysis_history sah
                    WHERE sah.stock_code = s.code
                    ORDER BY sah.created_at DESC LIMIT 1) as last_trade_strategy,
                   (SELECT sah.score FROM stock_analysis_history sah
                    WHERE sah.stock_code = s.code
                    ORDER BY sah.created_at DESC LIMIT 1) as last_score
            FROM stock_snapshot s
            WHERE s.code LIKE ? OR s.name LIKE ?
            LIMIT 20
            """,
            (pattern, pattern)
        ).fetchall()
    watched = WatchlistDAO().get_watched_codes()
    results = []
    for r in rows:
        item = dict(r)
        item['watched'] = item['code'] in watched
        # 解析最近一次交易信号
        item['last_signal'] = None
        if item.get('last_trade_strategy'):
            try:
                ts = json.loads(item['last_trade_strategy'])
                item['last_signal'] = ts.get('signal')
            except Exception:
                pass
        results.append(item)
    return results


@app.get("/api/watchlist")
async def api_watchlist():
    """钉选列表（带最新行情与最近 AI 信号）"""
    items = WatchlistDAO().list_all()
    # 合并实时行情缓存；无实时行情时用 stock_snapshot 的价格作 fallback
    realtime = get_realtime_cache()
    for item in items:
        code = item['code']
        if code in realtime:
            item['current_price'] = realtime[code].get('current_price')
            item['change_percent'] = realtime[code].get('change_percent')
        elif item.get('snapshot_price') is not None:
            item['current_price'] = item['snapshot_price']
    return items


@app.post("/api/watchlist/{code}")
async def api_watchlist_add(code: str):
    """加入钉选（从 stock_snapshot 取名称；若财务字段缺失则后台 enrich）

    被预过滤剔除的股票（如 PE 超范围）从未被 enrich，财务字段全空。
    钉选时检测 ROE 是否为空，空则后台异步拉取并回写，不阻塞响应。
    """
    with db_conn() as conn:
        row = conn.execute(
            "SELECT name, roe FROM stock_snapshot WHERE code = ?", (code,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="股票不存在")
    WatchlistDAO().add(code, row['name'])

    # 若 ROE 为空，后台异步 enrich（不阻塞当前请求）
    if row['roe'] is None:
        import threading
        def _enrich_bg():
            try:
                from src.collector.akshare_fetcher import enrich_financial_data, fetch_tencent_batch
                from src.models.database import StockSnapshotDAO
                quotes = fetch_tencent_batch([code])
                if quotes:
                    enrich_financial_data(quotes)
                    today = now_cn().strftime("%Y-%m-%d")
                    for q in quotes:
                        q['snapshot_date'] = q.get('snapshot_date') or today
                    StockSnapshotDAO().save_batch(quotes)
                    logger.info(f"[Watchlist] 后台 enrich 完成: {code}")
            except Exception as e:
                logger.warning(f"[Watchlist] 后台 enrich 失败 {code}: {e}")
        threading.Thread(target=_enrich_bg, daemon=True).start()

    return {"ok": True, "code": code, "name": row['name']}


@app.delete("/api/watchlist/{code}")
async def api_watchlist_remove(code: str):
    """取消钉选"""
    WatchlistDAO().remove(code)
    return {"ok": True, "code": code}


# ── AI 观察池 + 投资笔记 ──────────────────────────────────────

@app.get("/api/ai-watchlist")
async def api_ai_watchlist():
    """当前 AI 观察池（最多 5 只）"""
    items = AiWatchlistDAO().get_all()
    # 合并实时行情
    realtime = get_realtime_cache()
    for item in items:
        code = item['code']
        if code in realtime:
            item['current_price'] = realtime[code].get('current_price')
            item['change_percent'] = realtime[code].get('change_percent')
    return items


@app.get("/api/ai-watchlist/history")
async def api_ai_watchlist_history():
    """观察池调整历史"""
    return AiWatchlistHistoryDAO().list_recent(limit=50)


@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request):
    """投资笔记页（默认显示最新一篇）"""
    latest = AiJournalDAO().get_latest()
    history_list = AiJournalDAO().list_all()
    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')
    return templates.TemplateResponse(request, "journal.html", {
        "request": request,
        "page_title": page_title,
        "journal": latest,
        "history_list": history_list,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/journal/{journal_date}", response_class=HTMLResponse)
async def journal_by_date(request: Request, journal_date: str):
    """指定日期笔记页"""
    journal = AiJournalDAO().get_by_date(journal_date)
    history_list = AiJournalDAO().list_all()
    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')
    return templates.TemplateResponse(request, "journal.html", {
        "request": request,
        "page_title": page_title,
        "journal": journal,
        "history_list": history_list,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/api/journal/latest")
async def api_journal_latest():
    """最新笔记 JSON"""
    journal = AiJournalDAO().get_latest()
    if not journal:
        raise HTTPException(status_code=404, detail="无笔记")
    return journal


@app.get("/api/journal/list")
async def api_journal_list():
    """笔记列表（轻量，仅 date + title）"""
    return AiJournalDAO().list_all()


@app.get("/api/journal/{journal_date}")
async def api_journal_by_date(journal_date: str):
    """指定日期笔记 JSON"""
    journal = AiJournalDAO().get_by_date(journal_date)
    if not journal:
        raise HTTPException(status_code=404, detail="该日期无笔记")
    return journal


def run_server():
    """启动 Web 服务"""
    config = load_config()
    host = config.get('web', {}).get('host', '0.0.0.0')
    port = config.get('web', {}).get('port', 9527)

    # 确保数据库已初始化
    init_database()

    import uvicorn
    print(f"Stock Dashboard running at http://{host}:{port}")
    print(f"Market index: every 30min | Stock quote: every 5min (trading hours)")
    print(f"Daily pipeline: 15:30 (weekdays, Beijing time)")
    print(f"Press Ctrl+C to stop")
    uvicorn.run(app, host=host, port=port, log_level="info")
