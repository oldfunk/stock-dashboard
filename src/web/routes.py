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

        # 评分拆解（五维子分 + 一致性加分，2026-08-22 起落库 score_detail）
        if s.get('score_detail'):
            try:
                s['score_detail_parsed'] = (json.loads(s['score_detail'])
                                            if isinstance(s['score_detail'], str)
                                            else s['score_detail'])
            except (json.JSONDecodeError, TypeError):
                s['score_detail_parsed'] = None
        else:
            s['score_detail_parsed'] = None

        # 策略标签透传（多策略模式落库为 JSON array 字符串，单策略模式为 NULL）
        raw_tags = s.get('strategy_tags')
        s['strategy_tags'] = []
        if raw_tags:
            try:
                tags = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
                if isinstance(tags, list):
                    s['strategy_tags'] = [t for t in tags if isinstance(t, str)]
            except (json.JSONDecodeError, TypeError):
                s['strategy_tags'] = []

        # 当前分析所用模型名（ai_analysis JSON 顶层 model 字段）
        s['model'] = None
        if isinstance(s.get('ai_parsed'), dict):
            s['model'] = s['ai_parsed'].get('model')

        # 分析摘要前置：护城河类型 / 管理层评分 / 估值区间（仅透传，旧分析 NULL 行保持 None）
        s['moat_type'] = None
        s['mgmt_score'] = None
        s['iv_range'] = None
        if isinstance(s.get('ai_parsed'), dict):
            moats = s['ai_parsed'].get('moat_evaluation')
            if isinstance(moats, list) and moats and isinstance(moats[0], dict):
                s['moat_type'] = moats[0].get('type')
            s['mgmt_score'] = s['ai_parsed'].get('management_score')
            s['iv_range'] = s['ai_parsed'].get('intrinsic_value')

        # AI 分析失败标记（透明化：失败原因前端/复盘可见）
        s['ai_failed'] = bool(s.get('ai_failed') in (1, True, '1'))
        s['ai_failure_reason'] = s.get('ai_failure_reason') or None

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


def _from_json(text):
    """Jinja2 过滤器：JSON 字符串 → dict"""
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


templates.env.filters['from_json'] = _from_json

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# -------- 路由 --------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """看板首页"""
    from src.models.database import StockSnapshotDAO
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
    snap_dao = StockSnapshotDAO()
    sr_dao = ScreeningResultDAO()
    fs_dao = FinancialSummaryDAO()
    for item in ai_watchlist:
        code = item['code']
        # 实时行情：优先实时缓存，降级到 stock_snapshot
        item['current_price'] = None
        item['change_percent'] = None
        if code in realtime:
            item['current_price'] = realtime[code].get('current_price')
            item['change_percent'] = realtime[code].get('change_percent')
        else:
            snap = snap_dao.get_by_code(code)
            if snap:
                item['current_price'] = snap.get('current_price')

        # 从 screening_result 补全指标（pe/pb/roe/gross_margin/net_margin 等）
        sr = sr_dao.get_latest_for_code(code)
        if sr:
            item['pe'] = sr.get('pe')
            item['pb'] = sr.get('pb')
            item['roe'] = sr.get('roe')
            item['debt_ratio'] = sr.get('debt_ratio')
            item['market_cap'] = sr.get('market_cap')
            item['revenue_growth'] = sr.get('revenue_growth')
            item['profit_growth'] = sr.get('profit_growth')
            item['gross_margin'] = sr.get('gross_margin')
            item['net_margin'] = sr.get('net_margin')
            item['ocf_per_share'] = sr.get('ocf_per_share')
            item['score'] = sr.get('score')
            item['reason'] = sr.get('reason')
            # 评分拆解
            if sr.get('score_detail'):
                try:
                    item['score_detail_parsed'] = json.loads(sr['score_detail']) if isinstance(sr['score_detail'], str) else sr['score_detail']
                except (json.JSONDecodeError, TypeError):
                    item['score_detail_parsed'] = None
            else:
                item['score_detail_parsed'] = None
        else:
            for k in ('pe', 'pb', 'roe', 'debt_ratio', 'market_cap', 'revenue_growth',
                       'profit_growth', 'gross_margin', 'net_margin', 'ocf_per_share',
                       'score', 'reason', 'score_detail_parsed'):
                item[k] = None

        # 降级：stock_snapshot 补 pe/roe 等（筛选结果缺失时）
        snap = snap_dao.get_by_code(code)
        if snap:
            if item.get('pe') is None:
                item['pe'] = snap.get('pe')
            if item.get('pb') is None:
                item['pb'] = snap.get('pb')
            if item.get('roe') is None:
                item['roe'] = snap.get('roe')
            if item.get('debt_ratio') is None:
                item['debt_ratio'] = snap.get('debt_ratio')
            if item.get('market_cap') is None:
                item['market_cap'] = snap.get('market_cap')
            if item.get('revenue_growth') is None:
                item['revenue_growth'] = snap.get('revenue_growth')
            if item.get('profit_growth') is None:
                item['profit_growth'] = snap.get('profit_growth')

        # AI 分析（优先分析历史，降级到 screening_result）
        item['signal'] = None
        item['model'] = None
        item['ai_parsed'] = None
        item['trade_parsed'] = None
        item['ai_confidence'] = None
        item['ai_failed'] = False
        item['mirror_counts'] = None
        item['mirror_total'] = 0
        latest_hist = hist_dao.get_latest_for_code(code)
        if latest_hist and latest_hist.get('ai_trade_strategy'):
            try:
                trade = json.loads(latest_hist['ai_trade_strategy'])
                item['signal'] = trade.get('signal')
                if item.get('trade_parsed') is None:
                    item['trade_parsed'] = trade
                item['ai_confidence'] = trade.get('confidence')
            except (json.JSONDecodeError, TypeError):
                pass
        if latest_hist and latest_hist.get('ai_analysis'):
            try:
                analysis = json.loads(latest_hist['ai_analysis'])
                item['ai_parsed'] = analysis
                item['model'] = analysis.get('model')
                mc = analysis.get('mirror_counts')
                if mc is not None:
                    item['mirror_counts'] = str(mc)
                    import re as _re
                    nums = _re.findall(r'\d+', str(mc).split()[0] if ' ' in str(mc) else str(mc))
                    item['mirror_total'] = sum(int(n) for n in nums) if nums else 0
            except (json.JSONDecodeError, TypeError):
                pass
        if not item['signal'] and sr and sr.get('ai_trade_strategy'):
            try:
                trade = json.loads(sr['ai_trade_strategy'])
                item['signal'] = trade.get('signal')
                item['trade_parsed'] = trade
                item['ai_confidence'] = trade.get('confidence')
            except (json.JSONDecodeError, TypeError):
                pass
        if not item['ai_parsed'] and sr and sr.get('ai_analysis'):
            try:
                item['ai_parsed'] = json.loads(sr['ai_analysis'])
            except (json.JSONDecodeError, TypeError):
                pass
        if sr:
            item['ai_failed'] = bool(sr.get('ai_failed') in (1, True, '1'))
            item['ai_failure_reason'] = sr.get('ai_failure_reason') or None
        else:
            item['ai_failure_reason'] = None

        # 分析摘要前置（与 _enrich_stocks 同口径，观察池卡片统一渲染）
        item['moat_type'] = None
        item['mgmt_score'] = None
        item['iv_range'] = None
        if isinstance(item.get('ai_parsed'), dict):
            moats = item['ai_parsed'].get('moat_evaluation')
            if isinstance(moats, list) and moats and isinstance(moats[0], dict):
                item['moat_type'] = moats[0].get('type')
            item['mgmt_score'] = item['ai_parsed'].get('management_score')
            item['iv_range'] = item['ai_parsed'].get('intrinsic_value')

        # 分析历史时间线（最近5条）
        history = hist_dao.get_history(code, limit=5)
        parsed_history = []
        for h in history:
            if h.get('ai_analysis') and h['ai_analysis'] not in ['{}', '']:
                try:
                    ai_obj = json.loads(h['ai_analysis'])
                    h['hist_analysis'] = ai_obj.get('analysis', '') or ''
                    h['hist_strategy'] = ai_obj.get('investment_strategy', '') or ''
                    h['hist_trade'] = ai_obj.get('trade_strategy', {}) or {}
                    h['hist_mirror'] = ai_obj.get('mirror_counts', '') or ''
                except Exception:
                    pass
            parsed_history.append(h)
        item['analysis_history'] = parsed_history

        # 财务历史汇总（5y/10y）
        try:
            fs = fs_dao.get(code)
            if fs:
                item['_summary'] = {
                    'roe_5y_avg': fs.get('roe_5y_avg'),
                    'roe_10y_avg': fs.get('roe_10y_avg'),
                    'roe_improvement': fs.get('roe_improvement'),
                    'roe_volatility': fs.get('roe_volatility'),
                    'intcov_5y_avg': fs.get('intcov_5y_avg'),
                    'fcf_5y_sum': fs.get('fcf_5y_sum'),
                    'fcf_10y_sum': fs.get('fcf_10y_sum'),
                    'fcf_positive_years_10': fs.get('fcf_positive_years_10'),
                    'share_dilution_5y': fs.get('share_dilution_5y'),
                    'share_dilution_10y': fs.get('share_dilution_10y'),
                    'data_years': fs.get('data_years'),
                }
        except Exception:
            pass

    refresh = config.get('web', {}).get('refresh_interval', 30)

    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "page_title": page_title,
        "indices": indices,
        "stocks": stocks,
        "run_log": run_log,
        "refresh_interval": refresh,
        "ai_watchlist": ai_watchlist,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/candidates", response_class=HTMLResponse)
async def candidates(request: Request):
    """候选股总览页 — 显示最新筛选结果 + 所有历史分析过的股票

    数据源合并：
    1. screening_result 最新一轮（含完整财务字段）
    2. stock_analysis_history 中不在最新结果里的股票（按 code 去重取最新一条）

    用户可在 URL 加 ?days=N 过滤只看最近 N 天内分析过的股票。
    """
    # 可选：?days=N 只显示最近 N 天内分析过的股票
    days_param = request.query_params.get("days")
    days = int(days_param) if days_param and days_param.isdigit() else None

    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')

    # 1. 最新筛选结果（screening_result，含完整 pe/pb/roe 等财务字段）
    result_dao = ScreeningResultDAO()
    stocks = result_dao.get_latest_results()
    codes_in_results = {s['code'] for s in stocks}

    # 2. 历史分析过但不在最新筛选结果里的股票（补充进列表）
    hist_dao = StockAnalysisHistoryDAO()
    history_latest = hist_dao.get_all_latest(days=days)
    for h in history_latest:
        if h['stock_code'] not in codes_in_results:
            stocks.append({
                'code': h['stock_code'],
                'name': '',  # 历史表无 name 字段，留给 enrich 阶段从 screening_result 补
                'score': h.get('score') or 0,
                'ai_analysis': h.get('ai_analysis') or '',
                'ai_trade_strategy': h.get('ai_trade_strategy') or '',
                # 财务字段缺失，由 _enrich_stocks 的实时行情 + history 回退填空
                'pe': None, 'pb': None, 'roe': None, 'debt_ratio': None,
                'revenue_growth': None, 'profit_growth': None, 'market_cap': None,
                'gross_margin': None, 'net_margin': None, 'ocf_per_share': None,
                'reason': None,
            })

    # 3. 合并实时行情
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

    # 4. 解析 AI 分析 JSON + 历史 + 财务汇总（_enrich_stocks 会自动从历史回退填空）
    _enrich_stocks(stocks)

    # 5. 排序：score 降序（高分在前），score 相同按 code 升序
    stocks.sort(key=lambda s: (-(s.get('score') or 0), s['code']))

    return templates.TemplateResponse(request, "candidates.html", {
        "request": request,
        "page_title": page_title,
        "stocks": stocks,
        "days_filter": days,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/stock/{code}", response_class=HTMLResponse)
async def stock_detail(request: Request, code: str):
    """单股详情页 — 单栏叙事流

    展示单只股票的全部信息：关键指标快照 → AI 投资笔记 →
    护城河/管理层/内在价值/交易策略/逆向思考 → 动态财务历史 →
    历次 AI 分析时间线 → 在池状态。

    无 AI 分析时降级为纯数据版（财务 + 行业 + 估值）。
    """
    import re
    # 格式校验：6 位数字
    if not re.match(r"^\d{6}$", code):
        raise HTTPException(status_code=404, detail="Invalid code")

    from src.models.database import (
        StockSnapshotDAO, StockAnalysisHistoryDAO,
        FinancialHistoryDAO, FinancialSummaryDAO,
    )
    from src.models.ai_watchlist import (
        AiWatchlistDAO, AiWatchlistHistoryDAO
    )

    # 1. 基础快照 — 没有则 404
    snapshot = StockSnapshotDAO().get_by_code(code)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Stock not found")

    # 2. 实时行情缓存
    realtime = get_realtime_cache()
    realtime_data = realtime.get(code, {})

    # 3. 最新 AI 分析（如无则为 None，模板相应隐藏章节）
    latest_analysis = StockAnalysisHistoryDAO().get_latest_for_code(code)
    ai_parsed = None
    if latest_analysis and latest_analysis.get("ai_analysis"):
        try:
            ai_parsed = json.loads(latest_analysis["ai_analysis"])
        except (json.JSONDecodeError, TypeError):
            ai_parsed = None

    # 4. 最新交易策略
    trade_parsed = None
    if latest_analysis and latest_analysis.get("ai_trade_strategy"):
        try:
            trade_parsed = json.loads(latest_analysis["ai_trade_strategy"])
        except (json.JSONDecodeError, TypeError):
            trade_parsed = None

    # 5. 历次 AI 分析时间线（按日期倒序）
    analysis_history = StockAnalysisHistoryDAO().get_history(code, limit=20)

    # 6. 动态财务历史（年报，最新在前）
    annual_reports = FinancialHistoryDAO().get_annual_reports(code)
    financial_summary = FinancialSummaryDAO().get(code)

    # 兜底：首次访问未填充的股票，异步触发全量填充（下次刷新生效）
    if not annual_reports and not financial_summary:
        from src.collector.onboard import onboard_stock_async
        onboard_stock_async(code)

    # 7. 在池状态（如在池）
    in_watchlist = AiWatchlistDAO().get_by_code(code)
    watchlist_history = []
    if in_watchlist:
        watchlist_history = AiWatchlistHistoryDAO().list_by_code(code)

    # 8. 最新一轮筛选评分拆解（透明化：五维子分 + 一致性加分）
    from src.models.database import ScreeningResultDAO
    sr_latest = ScreeningResultDAO().get_latest_for_code(code)
    score_detail_parsed = None
    if sr_latest and sr_latest.get("score_detail"):
        try:
            score_detail_parsed = (json.loads(sr_latest["score_detail"])
                                   if isinstance(sr_latest["score_detail"], str)
                                   else sr_latest["score_detail"])
        except (json.JSONDecodeError, TypeError):
            score_detail_parsed = None

    config = load_config()
    page_title = config.get("web", {}).get("page_title", "价值投资选股看板")

    return templates.TemplateResponse(request, "stock_detail.html", {
        "request": request,
        "page_title": page_title,
        "code": code,
        "snapshot": snapshot,
        "realtime": realtime_data,
        "latest_analysis": latest_analysis,
        "ai_parsed": ai_parsed,
        "trade_parsed": trade_parsed,
        "analysis_history": analysis_history,
        "annual_reports": annual_reports,
        "financial_summary": financial_summary,
        "in_watchlist": in_watchlist,
        "watchlist_history": watchlist_history,
        "score_detail_parsed": score_detail_parsed,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


def _aggregate_kline(daily_records: list[dict], period: str) -> list[dict]:
    """将日K聚合为周K或月K

    period: "weekly" 按自然周（周一~周五）聚合
            "monthly" 按自然月聚合
    """
    from datetime import datetime

    if not daily_records:
        return []

    # 按周/月分组
    groups: dict[str, list[dict]] = {}
    for r in daily_records:
        dt = datetime.strptime(r["trade_date"], "%Y-%m-%d")
        if period == "weekly":
            # ISO 周号作为 key
            key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        else:  # monthly
            key = f"{dt.year}-{dt.month:02d}"
        groups.setdefault(key, []).append(r)

    result = []
    for key, group in groups.items():
        # 排序确保第一条是周/月第一天
        group.sort(key=lambda x: x["trade_date"])
        first = group[0]
        last = group[-1]
        result.append({
            "trade_date": first["trade_date"],
            "open": first.get("open"),
            "close": last.get("close"),
            "high": max((g.get("high") or 0) for g in group),
            "low": min((g.get("low") or 999999) for g in group),
            "volume": sum((g.get("volume") or 0) for g in group),
            "amount": sum((g.get("amount") or 0) for g in group),
            "turnover": sum((g.get("turnover") or 0) for g in group),
        })
    return result


def _to_klinecharts(records: list[dict]) -> list[dict]:
    """DB 记录转 klinecharts 所需格式。

    腾讯数据源无 volume/turnover，返回 None 会导致 klinecharts
    VOL 指标渲染失败，这里统一转为 0。
    """
    from datetime import datetime
    result = []
    for r in records:
        dt = datetime.strptime(r["trade_date"], "%Y-%m-%d")
        result.append({
            "timestamp": int(dt.replace(hour=15).timestamp() * 1000),
            "open": r.get("open") or 0,
            "close": r.get("close") or 0,
            "high": r.get("high") or 0,
            "low": r.get("low") or 0,
            "volume": r.get("volume") or 0,
            "turnover": r.get("turnover") or 0,
        })
    return result


@app.get("/api/stock/{code}/kline")
async def stock_kline(code: str, period: str = "daily", limit: int = 250):
    """K线数据 API

    period: daily / weekly / monthly
    返回 klinecharts 所需格式
    """
    import re
    if not re.match(r"^\d{6}$", code):
        raise HTTPException(status_code=404, detail="Invalid code")
    if period not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="period must be daily/weekly/monthly")

    from src.models.database import KlineDAO
    dao = KlineDAO()
    daily = dao.get_daily(code, limit=limit)

    if period != "daily":
        daily = _aggregate_kline(daily, period)

    klines = _to_klinecharts(daily)
    return {"code": code, "period": period, "klines": klines}


@app.get("/api/index/{index_code}/kline")
async def index_kline(index_code: str, period: str = "daily", limit: int = 250):
    """指数 K 线数据 API（实时从 akshare 拉取，不缓存）。

    index_code: sh000001 / sz399001 等
    period: daily / weekly / monthly
    """
    import re
    if not re.match(r"^(sh|sz)\d{6}$", index_code):
        raise HTTPException(status_code=400, detail="Invalid index code")
    if period not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="period must be daily/weekly/monthly")

    from src.collector.akshare_fetcher import fetch_index_kline
    daily = fetch_index_kline(index_code, limit=limit)
    if period != "daily":
        daily = _aggregate_kline(daily, period)
    klines = _to_klinecharts(daily)
    return {"code": index_code, "period": period, "klines": klines}


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
    """加入钉选（从 stock_snapshot 取名称；若财务字段缺失则后台全量填充）

    被预过滤剔除的股票（如 PE 超范围）从未被 enrich，财务字段全空。
    钉选时检测 ROE 是否为空，空则后台异步执行 onboard_stock 全量填充，不阻塞响应。
    """
    with db_conn() as conn:
        row = conn.execute(
            "SELECT name, roe FROM stock_snapshot WHERE code = ?", (code,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="股票不存在")
    WatchlistDAO().add(code, row['name'])

    # 若 ROE 为空，后台全量填充（不阻塞当前请求）
    if row['roe'] is None:
        from src.collector.onboard import onboard_stock_async
        onboard_stock_async(code)
        logger.info(f"[Watchlist] 触发全量填充: {code}")

    return {"ok": True, "code": code, "name": row['name']}


@app.delete("/api/watchlist/{code}")
async def api_watchlist_remove(code: str):
    """取消钉选"""
    WatchlistDAO().remove(code)
    return {"ok": True, "code": code}


@app.get("/api/watchlist/{code}/monitor")
async def api_watchlist_monitor_get(code: str):
    """读取监控条件（JSON 字符串）"""
    cond = AiWatchlistDAO().get_monitor_condition(code)
    return {"code": code, "monitor_condition": cond}


@app.post("/api/watchlist/{code}/monitor")
async def api_watchlist_monitor_update(code: str, body: dict):
    """更新监控条件。body: {"monitor_condition": "{...}" 或 null 清空"""
    cond = body.get("monitor_condition")
    if cond is not None and not isinstance(cond, str):
        raise HTTPException(status_code=400, detail="monitor_condition 必须为 JSON 字符串或 null")
    ok = AiWatchlistDAO().update_monitor_condition(code, cond)
    if not ok:
        raise HTTPException(status_code=404, detail="股票不在观察池")
    return {"ok": True, "code": code, "monitor_condition": cond}


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


@app.get("/watchlist/{code}")
async def watchlist_detail(request: Request, code: str):
    """钉选股独立分析页面 — 复用 stock_detail 的取数模式，统一数据接口"""
    import re
    if not re.match(r"^\d{6}$", code):
        raise HTTPException(status_code=404, detail="Invalid code")

    from src.models.database import (
        StockSnapshotDAO, StockAnalysisHistoryDAO,
        FinancialHistoryDAO, FinancialSummaryDAO, ScreeningResultDAO,
    )

    # 1. 基础快照 — 没有则 404
    snapshot = StockSnapshotDAO().get_by_code(code)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Stock not found")

    # 2. 实时行情缓存
    realtime = get_realtime_cache()
    realtime_data = realtime.get(code, {})

    # 3. 历次 AI 分析时间线（按日期倒序）
    analysis_history = StockAnalysisHistoryDAO().get_history(code, limit=20)

    # 4. 最新 AI 分析（用于投资人笔记/护城河/估值/交易策略）
    latest_analysis = StockAnalysisHistoryDAO().get_latest_for_code(code)
    ai_parsed = None
    trade_parsed = None
    if latest_analysis and latest_analysis.get("ai_analysis"):
        try:
            ai_parsed = json.loads(latest_analysis["ai_analysis"])
        except (json.JSONDecodeError, TypeError):
            ai_parsed = None
    if latest_analysis and latest_analysis.get("ai_trade_strategy"):
        try:
            trade_parsed = json.loads(latest_analysis["ai_trade_strategy"])
        except (json.JSONDecodeError, TypeError):
            trade_parsed = None

    # 5. 最新一轮筛选评分拆解（透明化：五维子分 + 一致性加分）
    sr_latest = ScreeningResultDAO().get_latest_for_code(code)
    score_detail_parsed = None
    if sr_latest and sr_latest.get("score_detail"):
        try:
            score_detail_parsed = (json.loads(sr_latest["score_detail"])
                                   if isinstance(sr_latest["score_detail"], str)
                                   else sr_latest["score_detail"])
        except (json.JSONDecodeError, TypeError):
            score_detail_parsed = None

    # 6. 动态财务历史（年报，最新在前）
    annual_reports = FinancialHistoryDAO().get_annual_reports(code)
    financial_summary = FinancialSummaryDAO().get(code)

    # 7. 在池状态（如在池）
    in_watchlist = AiWatchlistDAO().get_by_code(code)
    watchlist_history = []
    if in_watchlist:
        watchlist_history = AiWatchlistHistoryDAO().list_by_code(code, limit=20)

    page_title = f"{snapshot.get('name', code)} {code} - 钉选股分析"

    return templates.TemplateResponse(request, "watchlist_detail.html", {
        "page_title": page_title,
        "code": code,
        "snapshot": snapshot,
        "realtime": realtime_data,
        "in_watchlist": in_watchlist,
        "watchlist_history": watchlist_history,
        "analysis_history": analysis_history,
        "score_detail_parsed": score_detail_parsed,
        "ai_parsed": ai_parsed,
        "trade_parsed": trade_parsed,
        "financial_summary": financial_summary,
        "annual_reports": annual_reports,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


# 投资笔记路由已移除（2026-09-20 路线调整，AI 分析缩减为 API 接口）
# @app.get("/journal", response_class=HTMLResponse)
# async def journal_page(request: Request):
#     """投资笔记页（默认显示最新一篇）"""
#     latest = AiJournalDAO().get_latest()
#     history_list = AiJournalDAO().list_all()
#     config = load_config()
#     page_title = config.get('web', {}).get('page_title', '价值投资选股看板')
#     return templates.TemplateResponse(request, "journal.html", {
#         "request": request,
#         "page_title": page_title,
#         "journal": latest,
#         "history_list": history_list,
#         "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
#     })


# 投资笔记 API 已移除（2026-09-20 路线调整）
# @app.get("/api/journal/latest")
# async def api_journal_latest():
#     """最新笔记 JSON"""
#     journal = AiJournalDAO().get_latest()
#     if not journal:
#         raise HTTPException(status_code=404, detail="无笔记")
#     return journal


# @app.get("/api/journal/list")
# async def api_journal_list():
#     """笔记列表（轻量，仅 date + title）"""
#     return AiJournalDAO().list_all()


# @app.get("/api/journal/{journal_date}")
# async def api_journal_by_date(journal_date: str):
#     """指定日期笔记 JSON（缺失 404，与 /latest 一致）"""
#     journal = AiJournalDAO().get_by_date(journal_date)
#     if not journal:
#         raise HTTPException(status_code=404, detail="Journal not found")
#     return journal


# _detect_pool_drift 已移除（仅被已注释的 journal conflicts 路由调用）


# 投资笔记矛盾检测 API 已移除（2026-09-20 路线调整）
# @app.get("/api/journal/{journal_date}/conflicts")
# async def api_journal_conflicts(journal_date: str):
#     """笔记矛盾检测分析"""
#     journal = AiJournalDAO().get_by_date(journal_date)
#     if not journal:
#         return {"error": "Journal not found"}
#     
#     previous = AiJournalDAO().get_previous(journal_date)
#     
#     if not previous:
#         return {"has_conflicts": False, "message": "第一期笔记，无可对比"}
#     
#     # 简单的矛盾检测逻辑
#     conflicts = []
#     
#     # 检查标题变化
#     if journal['title'] != previous['title']:
#         conflicts.append({
#             "type": "title_change",
#             "message": f"标题从 '{previous['title']}' 变为 '{journal['title']}'",
#             "severity": "info"
#         })
#     
#     # 检查内容长度变化
#     content_length_change = len(journal['content_md']) - len(previous['content_md'])
#     if abs(content_length_change) > 500:
# 投资笔记矛盾检测 API 已移除（2026-09-20 路线调整）


# 虚拟盘路由已移除（2026-09-20 路线调整，量化交易系统归档）
# @app.get("/paper", response_class=HTMLResponse)
# async def paper_page(request: Request):
#     """虚拟盘面板（只读）：账户 + 持仓 + 最近委托 + 最新净值。"""
#     import sqlite3
#     from src.models.database import (
#         PaperAccountDAO, PaperOrderDAO, PaperPositionDAO, PaperNavDAO,
#         StockSnapshotDAO,
#     )
#     paper_ready = True
#     account, positions, orders, nav_latest = None, [], [], None
#     try:
#         account = PaperAccountDAO().get()
#         positions = PaperPositionDAO().list_all()
#         orders = PaperOrderDAO().list_recent(limit=50)
#         nav_latest = PaperNavDAO().get_latest()
#         snap_dao = StockSnapshotDAO()
#         for p in positions:
#             snap = snap_dao.get_by_code(p["code"])
#             p["name"] = (snap or {}).get("name") or ""
#     except sqlite3.OperationalError:
#         paper_ready = False
#         account, positions, orders, nav_latest = None, [], [], None
#     config = load_config()
#     page_title = config.get('web', {}).get('page_title', '价值投资选股看板')
#     return templates.TemplateResponse(request, "paper.html", {
#         "request": request,
#         "page_title": page_title,
#         "paper_ready": paper_ready,
#         "account": account,
#         "positions": positions,
#         "orders": orders,
#         "nav_latest": nav_latest,
#         "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
#     })


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
