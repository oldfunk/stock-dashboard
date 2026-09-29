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
from pydantic import BaseModel

import src.llm_config as llm_config

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


# 监控条件 JSON→人话（观察池黄框展示用；解析失败原样返回，不丢信息）
_MONITOR_METRIC_CN = {
    "pe": "PE", "roe": "ROE", "gross_margin": "毛利率",
    "net_margin": "净利率", "debt_ratio": "负债率",
    "dividend_yield": "股息率", "roe_volatility": "ROE波动",
    "fcf_yield": "FCF收益率", "current_price": "现价",
}
_MONITOR_OP_CN = {"lt": "<", "le": "≤", "gt": ">", "ge": "≥", "eq": "="}


def format_monitor_condition(raw) -> str | None:
    """把 monitor_condition 存的 JSON（如 {"metric":"pe","operator":"lt",
    "threshold":20}）转成 “PE < 20 时提醒”；空值返回 None，
    解析失败/字段不全原样返回 raw。"""
    if not raw:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        cond = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(cond, dict):
        return raw
    metric = _MONITOR_METRIC_CN.get(cond.get("metric"))
    op = _MONITOR_OP_CN.get(cond.get("operator"))
    threshold = cond.get("threshold")
    if metric is None or op is None or threshold is None:
        return raw
    return f"{metric} {op} {threshold} 时提醒"


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

        # 数据质量标注（供外部 AI 消费：每个数字的来源/日期/置信度，见 ai_analyzer._data_quality_facts）
        try:
            from src.analyzer.ai_analyzer import _data_quality_facts
            s['data_quality'] = _data_quality_facts(s)
        except Exception:
            s['data_quality'] = None

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
    """看板首页 — 显示候选股总览（最新筛选结果 + 历史分析过的股票）"""
    from src.models.database import StockSnapshotDAO
    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')

    # 获取最新大盘数据
    market_dao = MarketIndexDAO()
    indices = market_dao.get_latest()

    # 获取最新筛选结果
    result_dao = ScreeningResultDAO()
    stocks = result_dao.get_latest_results()
    codes_in_results = {s['code'] for s in stocks}

    # 历史分析过但不在最新筛选结果里的股票（补充进列表）
    hist_dao = StockAnalysisHistoryDAO()
    history_latest = hist_dao.get_all_latest()
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

    # 行业均值参照（P1② 评分透明化收尾）：板块归属 + 各板块均值，只读展示
    sector_map = StockSnapshotDAO.get_sector_map()
    sector_avg = StockSnapshotDAO.get_sector_averages()
    for s in stocks:
        s['sector'] = sector_map.get(s['code'])

    # 排序：score 降序（高分在前），score 相同按 code 升序
    stocks.sort(key=lambda s: (-(s.get('score') or 0), s['code']))

    # 获取 AI 观察池 + 合并实时行情 + 指标（只展示数据；AI 分析由外部 AI 通过 API 消费，2026-09-21 方向）
    ai_watchlist = AiWatchlistDAO().get_all()
    realtime = get_realtime_cache()
    snap_dao = StockSnapshotDAO()
    sr_dao = ScreeningResultDAO()
    fs_dao = FinancialSummaryDAO()
    for item in ai_watchlist:
        code = item['code']
        item['sector'] = sector_map.get(code)
        # 监控条件人话文案（黄框展示；无条件则为 None，模板回退原样）
        item['monitor_text'] = format_monitor_condition(
            item.get('monitor_condition'))
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

        # 注：AI 分析字段（signal/ai_parsed/trade_parsed/分析历史/摘要前置）已于 2026-09-21
        # 从观察池 enrichment 移除——外部 AI 通过 /api/watchlist/{code}/full 等接口自取，
        # 列表卡片只展示数据。历史 AI 文本由外部 AI 读库追溯。

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
        "sector_avg": sector_avg,
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

    # 行业均值参照（P1② 评分透明化收尾）：本股板块均值，只读展示
    sector_avg = None
    _sector = snapshot.get("sector")
    if _sector:
        _row = StockSnapshotDAO.get_sector_averages().get(_sector)
        if _row:
            sector_avg = {"sector": _sector, **_row}

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
        "sector_avg": sector_avg,
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


# ── LLM 自带 Key 接入 ──────────────────────────────────────────

class LLMTestRequest(BaseModel):
    api_base: str = ""
    api_key: str = ""


class LLMSaveRequest(BaseModel):
    provider: str = "custom"
    api_base: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 6000


@app.get("/api/llm/providers")
async def llm_providers():
    """厂商预设列表（无敏感信息）"""
    return {"providers": llm_config.PROVIDERS}


@app.post("/api/llm/test")
async def llm_test(req: LLMTestRequest):
    """连接测试 + 拉模型列表（不持久化，Key 不记日志）"""
    try:
        models = llm_config.fetch_models(req.api_base, req.api_key or "")
    except llm_config.LLMSetupError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "models": models}


@app.post("/api/llm/save")
async def llm_save(req: LLMSaveRequest):
    """保存 LLM 配置：Key → .env，非敏感 → local.yaml；返回脱敏状态"""
    try:
        return llm_config.save_llm_config(
            req.provider, req.api_base, req.model, req.api_key or "",
            req.temperature, req.max_tokens)
    except llm_config.LLMSetupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/llm/status")
async def llm_status():
    """LLM 配置状态（脱敏：只有 has_key 布尔值 + key_preview）"""
    return llm_config.llm_status()


# ── LLM 分析队列 + 自定义分析 ──────────────────────────────────
def _check_interval(v) -> int:
    try:
        iv = int(v or 60)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="interval_seconds 必须是整数")
    if not 5 <= iv <= 600:
        raise HTTPException(status_code=400, detail="interval_seconds 须在 5~600 之间")
    return iv


class LLMAnalyzeRequest(BaseModel):
    mode: str = "all"
    code: str = ""
    interval_seconds: int = 60


class LLMEnqueueRequest(BaseModel):
    scope: str = "candidates"
    codes: str = ""
    requirement: str = ""
    interval_seconds: int = 60


class LLMCancelRequest(BaseModel):
    task_id: str = ""


class LLMPolishRequest(BaseModel):
    text: str = ""


class LLMMarketRequest(BaseModel):
    requirement: str = ""


class LLMConcurrencyRequest(BaseModel):
    concurrency: int = 2


def _enrich_with_financial_summary(stocks: list) -> None:
    """run_ai_analysis.py 同款富集：financial_summary 字段注入 stock（原地修改）。"""
    from src.models.database import FinancialSummaryDAO
    fs_dao = FinancialSummaryDAO()
    for s in stocks:
        fs = fs_dao.get(s['code'])
        if fs:
            for k, v in fs.items():
                if k not in ('stock_code', 'updated_at') and v is not None:
                    s[k] = v


def _resolve_scope(scope: str, codes_text: str = "") -> tuple:
    """范围解析 → (stocks, note, run_id)。stocks 未富集；调用方按需富集。"""
    import re
    from src.models.database import (
        ScreeningResultDAO, StockSnapshotDAO, WatchlistDAO, RunLogDAO)
    scope = (scope or "candidates").strip()
    if scope not in ("candidates", "watchlist", "pool", "custom"):
        raise HTTPException(status_code=400, detail="scope 非法：candidates|watchlist|pool|custom")
    run_id = RunLogDAO().get_latest_completed_run_id()
    if scope == "candidates":
        if not run_id:
            raise HTTPException(status_code=400, detail="暂无已完成的筛选批次")
        stocks = ScreeningResultDAO().get_results_for_run(run_id)
        return stocks, f"最新轮 {len(stocks)} 只", run_id
    if scope == "watchlist":
        codes = sorted(WatchlistDAO().get_watched_codes())
        label = "钉选"
    elif scope == "pool":
        from src.models.ai_watchlist import AiWatchlistDAO
        codes = sorted({x.get("code") for x in AiWatchlistDAO().get_all() if x.get("code")})
        label = "观察池"
    else:
        codes = sorted(set(re.findall(r"\d{6}", codes_text or "")))
        if not codes:
            raise HTTPException(status_code=400, detail="自定义代码为空（填 6 位代码，多个用空格/换行分隔）")
        if len(codes) > 50:
            raise HTTPException(status_code=400, detail="自定义最多 50 只")
        label = "自定义"
    dao = StockSnapshotDAO()
    stocks, missing = [], []
    for c in codes:
        row = dao.get_by_code(c)
        if row:
            stocks.append(dict(row))
        else:
            missing.append(c)
    note = f"{label} {len(stocks)} 只" + (f"（快照无：{','.join(missing)}）" if missing else "")
    return stocks, note, run_id


@app.post("/api/llm/analyze")
async def llm_analyze(req: LLMAnalyzeRequest):
    """单次触发（卡片/兼容）：all 整轮 / retry 只补失败 / once 单股，走队列，返回 task_id。"""
    from src import ai_queue
    from src.analyzer.ai_analyzer import AiAnalyzer
    mode = (req.mode or "all").strip()
    if mode not in ("all", "retry", "once"):
        raise HTTPException(status_code=400, detail="mode 非法：all|retry|once")
    interval = _check_interval(req.interval_seconds)
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    if not analyzer.configured:
        raise HTTPException(status_code=400, detail="未配置 API Key，先在模型设置页保存")
    if mode == "once":
        code = (req.code or "").strip()
        if len(code) != 6 or not code.isdigit():
            raise HTTPException(status_code=400, detail="code 须为 6 位数字")
        stocks, note, run_id = _resolve_scope("custom", code)
        if not stocks:
            raise HTTPException(status_code=404, detail=f"本轮无此股票：{code}")
        name = f"单股{code}"
    else:
        stocks, note, run_id = _resolve_scope("candidates", "")
        if mode == "retry":
            stocks = [s for s in stocks if s.get('ai_failed')]
            if not stocks:
                return {"status": "noop", "message": "本轮无失败项，无需补跑"}
        name = "整轮分析" if mode == "all" else "补跑失败"
        if not stocks:
            raise HTTPException(status_code=400, detail="本轮无候选股票")
    if not run_id:
        run_id = now_cn().strftime("%Y%m%d_%H%M%S")
    tid = ai_queue.get_queue().submit(name, mode, stocks, run_id, interval,
                                      llm_config.get_concurrency())
    return {"status": "started", "mode": mode, "total": len(stocks),
            "run_id": run_id, "task_id": tid, "note": note}


@app.post("/api/llm/enqueue")
async def llm_enqueue(req: LLMEnqueueRequest):
    """入队：scope 范围 + 自定义要求（润色后文本）+ 间隔。"""
    from src import ai_queue
    from src.analyzer.ai_analyzer import AiAnalyzer
    interval = _check_interval(req.interval_seconds)
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    if not analyzer.configured:
        raise HTTPException(status_code=400, detail="未配置 API Key，先在模型设置页保存")
    extra = (req.requirement or "").strip()[:2000] or None
    stocks, note, run_id = _resolve_scope(req.scope, req.codes)
    if not stocks:
        raise HTTPException(status_code=400, detail=f"范围无股票（{note}）")
    if not run_id:
        run_id = now_cn().strftime("%Y%m%d_%H%M%S")
    tid = ai_queue.get_queue().submit(note, req.scope, stocks, run_id, interval,
                                      llm_config.get_concurrency(), extra)
    return {"status": "queued", "task_id": tid, "total": len(stocks), "note": note}


@app.get("/api/llm/queue")
async def llm_queue():
    """队列快照 + 当前并发数。"""
    from src import ai_queue
    return {"tasks": ai_queue.get_queue().snapshot(),
            "workers": llm_config.get_concurrency()}


@app.post("/api/llm/queue/cancel")
async def llm_queue_cancel(req: LLMCancelRequest):
    """取消排队中的任务（运行中的停不下来，返回 false）。"""
    from src import ai_queue
    tid = (req.task_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    return {"cancelled": ai_queue.get_queue().cancel(tid)}


@app.post("/api/llm/polish")
async def llm_polish(req: LLMPolishRequest):
    """需求润色：口语 → 结构化分析指令。"""
    from src import ai_queue
    from src.analyzer.ai_analyzer import AiAnalyzer
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="需求不能为空")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="需求超长（≤2000 字）")
    if not AiAnalyzer(load_config().get('ai', {})).configured:
        raise HTTPException(status_code=400, detail="未配置 API Key，先在模型设置页保存")
    out = ai_queue.polish_requirement(text)
    if out is None:
        raise HTTPException(status_code=502, detail="润色失败（见服务端日志）")
    return {"polished": out}


@app.post("/api/llm/market")
async def llm_market(req: LLMMarketRequest):
    """大盘解盘写笔记（后台跑，写 ai_journal；同日复盘行追加不覆盖）。"""
    from src import ai_queue
    from src.analyzer.ai_analyzer import AiAnalyzer
    if len(req.requirement or "") > 2000:
        raise HTTPException(status_code=400, detail="要求超长（≤2000 字）")
    if not AiAnalyzer(load_config().get('ai', {})).configured:
        raise HTTPException(status_code=400, detail="未配置 API Key，先在模型设置页保存")
    jid = ai_queue.submit_market_note(req.requirement)
    return {"status": "started", "job_id": jid}


@app.get("/api/llm/market-note/{jid}")
async def llm_market_note_status(jid: str):
    """解盘笔记任务状态（done 时带 journal_date/用量）。"""
    from src import ai_queue
    out = ai_queue.get_market_note_job((jid or "").strip())
    if out is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return out


@app.post("/api/llm/concurrency")
async def llm_concurrency(req: LLMConcurrencyRequest):
    """设置并发数（1~5，写 local.yaml，下个任务生效）。"""
    try:
        n = llm_config.set_concurrency(req.concurrency)
    except llm_config.LLMSetupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"concurrency": n}


@app.get("/api/llm/usage")
async def llm_usage(run_id: str = ""):
    """token 用量：某轮每只股的 prompt/completion + 合计（默认最新完成轮）"""
    from src.models.database import AiAnalysisLogDAO
    run_id = (run_id or "").strip() or RunLogDAO().get_latest_completed_run_id() or ""
    if not run_id:
        return {"run_id": "", "rows": [], "count": 0,
                "total_prompt": 0, "total_completion": 0}
    rows = AiAnalysisLogDAO().get_by_run(run_id)
    return {"run_id": run_id, "rows": rows, "count": len(rows),
            "total_prompt": sum(r.get("prompt_tokens", 0) or 0 for r in rows),
            "total_completion": sum(r.get("completion_tokens", 0) or 0 for r in rows)}


class LLMAskRequest(BaseModel):
    code: str = ""
    question: str = ""


@app.post("/api/llm/ask")
async def llm_ask(req: LLMAskRequest):
    """单股自由问答（不落库 analyzed，只 inline 返回 + 用量；存笔记走现有 notes 接口）。"""
    from src.analyzer.ai_analyzer import AiAnalyzer, answer_question
    from src.models.database import StockSnapshotDAO
    code = (req.code or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=400, detail="code 须为 6 位数字")
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    if len(question) > 500:
        raise HTTPException(status_code=400, detail="问题超长（≤500 字）")
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    if not analyzer.configured:
        raise HTTPException(status_code=400, detail="未配置 API Key，先在模型设置页保存")
    snap = StockSnapshotDAO().get_by_code(code)
    if not snap:
        raise HTTPException(status_code=404, detail=f"快照无此股票：{code}")
    stock = dict(snap)
    latest = ScreeningResultDAO().get_latest_for_code(code)
    if latest:
        for k in ('score', 'reason', 'pe', 'pb', 'roe', 'market_cap'):
            if stock.get(k) in (None, '') and latest.get(k) not in (None, ''):
                stock[k] = latest[k]
    _enrich_with_financial_summary([stock])
    out = answer_question(stock, question)
    if out is None:
        raise HTTPException(status_code=502, detail="问答失败（见服务端日志）")
    return {"ok": True, "code": code, "answer": out["answer"],
            "model": out.get("model"), "usage": out.get("usage") or {}}


# ── 纸盘模拟页（直达子项目原面板；原生回测引擎已随 M6 转向删除） ──


@app.get("/paper", response_class=HTMLResponse)
async def paper_page(request: Request):
    """模拟交易页：直达子项目原面板（:8081）。"""
    config = load_config()
    return templates.TemplateResponse(request, "paper.html", {
        "request": request,
        "page_title": config.get("web", {}).get("page_title", "价值投资选股看板"),
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/llm", response_class=HTMLResponse)
async def llm_page(request: Request):
    """模型设置页：厂商/Key/模型 + 高级参数（执行已移至首页 AI 面板）"""
    config = load_config()
    return templates.TemplateResponse(request, "llm.html", {
        "request": request,
        "page_title": config.get("web", {}).get("page_title", "价值投资选股看板"),
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


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


# ── 钉选股票投资笔记（AI 可访问） ──────────────────────────────

@app.get("/api/watchlist/{code}/notes")
async def api_watchlist_notes_get(code: str, limit: int = 10):
    """获取某只钉选股的投资笔记列表（AI 或用户提交）"""
    notes = WatchlistDAO().list_notes(code, limit=limit)
    return {"code": code, "notes": notes}


@app.post("/api/watchlist/{code}/notes")
async def api_watchlist_notes_add(code: str, body: dict):
    """添加投资笔记（AI 或用户提交）

    body: {"note": "...", "note_type": "weekly/analysis/user", "model": "模型标识（外部 AI 必填溯源）"}
    """
    note = body.get("note")
    if not note or not isinstance(note, str):
        raise HTTPException(status_code=400, detail="note 必须为非空字符串")
    note_type = body.get("note_type", "weekly")
    if note_type not in ("weekly", "analysis", "user"):
        raise HTTPException(status_code=400, detail="note_type 必须为 weekly/analysis/user")
    model = body.get("model")
    if model is not None and not isinstance(model, str):
        raise HTTPException(status_code=400, detail="model 必须为字符串")
    ok = WatchlistDAO().add_note(code, note, note_type, model)
    if not ok:
        raise HTTPException(status_code=500, detail="添加笔记失败")
    return {"ok": True, "code": code, "note_type": note_type, "model": model}


@app.get("/api/watchlist/notes")
async def api_watchlist_all_notes(limit: int = 50):
    """获取所有钉选股的投资笔记（便于 AI 批量获取）"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist_notes ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return {"notes": [dict(r) for r in rows]}


@app.get("/api/watchlist/{code}/thesis")
async def api_watchlist_thesis_get(code: str):
    """获取某只股票的投资论文（论点+假设+红线+卖出条件）"""
    from src.models.database import WatchlistThesisDAO
    return {"code": code, "thesis": WatchlistThesisDAO.get(code)}


@app.post("/api/watchlist/{code}/thesis")
async def api_watchlist_thesis_upsert(code: str, body: dict):
    """创建/更新投资论文（外部 AI 或本地分析提交）

    body: {"core_thesis": "...", "assumptions": [{content, verify_method,
    verify_freq, status}], "red_lines": [{condition, action}],
    "sell_conditions": ["..."], "source": "ai_analysis/manual"}
    """
    from src.models.database import WatchlistThesisDAO
    core = body.get("core_thesis")
    if not core or not isinstance(core, str):
        raise HTTPException(status_code=400, detail="core_thesis 必须为非空字符串")
    for key in ("assumptions", "red_lines", "sell_conditions"):
        if key in body and not isinstance(body[key], list):
            raise HTTPException(status_code=400, detail=f"{key} 必须为数组")
    source = body.get("source", "manual")
    if source not in ("ai_analysis", "manual"):
        raise HTTPException(status_code=400, detail="source 必须为 ai_analysis/manual")
    thesis = WatchlistThesisDAO.upsert(
        code, core[:500], body.get("assumptions") or [],
        body.get("red_lines") or [], body.get("sell_conditions") or [],
        source=source)
    return {"ok": True, "code": code, "thesis": thesis}


@app.get("/api/watchlist/{code}/full")
async def api_watchlist_full(code: str):
    """获取钉选股的完整数据（一次性获取所有信息供 AI 分析）

    返回：
    - 基本信息：code, name, added_at, note, latest_price, last_signal
    - 实时行情：current_price, change_percent, change_amount
    - 财务指标：pe, pb, roe, debt_ratio, market_cap, revenue_growth, profit_growth, gross_margin, net_margin
    - AI 分析：ai_parsed（完整 JSON）, trade_parsed, model, ai_failed
    - 分析历史：analysis_history（最近 5 条）
    - 投资笔记：notes（最近 10 条）
    """
    from src.models.database import (
        WatchlistDAO, StockSnapshotDAO, ScreeningResultDAO,
        StockAnalysisHistoryDAO, FinancialSummaryDAO, RunLogDAO
    )
    from src.utils import now_cn

    # 1. 基本信息
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE code = ?", (code,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="股票不在钉选列表")
    item = dict(row)

    # 2. 实时行情
    realtime = get_realtime_cache()
    if code in realtime:
        item['current_price'] = realtime[code].get('current_price')
        item['change_percent'] = realtime[code].get('change_percent')
        item['change_amount'] = realtime[code].get('change_amount')
    else:
        item['current_price'] = None
        item['change_percent'] = None
        item['change_amount'] = None

    # 3. 财务指标（screening_result + stock_snapshot 降级）
    sr = ScreeningResultDAO().get_latest_for_code(code)
    snap = StockSnapshotDAO().get_by_code(code)
    item['pe'] = sr.get('pe') if sr else (snap.get('pe') if snap else None)
    item['pb'] = sr.get('pb') if sr else (snap.get('pb') if snap else None)
    item['roe'] = sr.get('roe') if sr else (snap.get('roe') if snap else None)
    item['debt_ratio'] = sr.get('debt_ratio') if sr else (snap.get('debt_ratio') if snap else None)
    item['market_cap'] = sr.get('market_cap') if sr else (snap.get('market_cap') if snap else None)
    item['revenue_growth'] = sr.get('revenue_growth') if sr else None
    item['profit_growth'] = sr.get('profit_growth') if sr else None
    item['gross_margin'] = sr.get('gross_margin') if sr else None
    item['net_margin'] = sr.get('net_margin') if sr else None
    item['score'] = sr.get('score') if sr else None
    item['reason'] = sr.get('reason') if sr else None

    # 4. AI 分析（优先历史，降级到 screening_result）
    hist_dao = StockAnalysisHistoryDAO()
    latest_hist = hist_dao.get_latest_for_code(code)
    item['signal'] = None
    item['model'] = None
    item['ai_parsed'] = None
    item['trade_parsed'] = None
    item['ai_confidence'] = None
    item['ai_failed'] = False

    if latest_hist and latest_hist.get('ai_trade_strategy'):
        try:
            trade = json.loads(latest_hist['ai_trade_strategy'])
            item['signal'] = trade.get('signal')
            item['trade_parsed'] = trade
            item['ai_confidence'] = trade.get('confidence')
        except (json.JSONDecodeError, TypeError):
            pass
    if latest_hist and latest_hist.get('ai_analysis'):
        try:
            analysis = json.loads(latest_hist['ai_analysis'])
            item['ai_parsed'] = analysis
            item['model'] = analysis.get('model')
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

    # 5. 分析历史时间线（最近 5 条）
    history = hist_dao.get_history(code, limit=5)
    parsed_history = []
    for h in history:
        if h.get('ai_analysis') and h['ai_analysis'] not in ['{}', '']:
            try:
                ai_obj = json.loads(h['ai_analysis'])
                h['hist_analysis'] = ai_obj.get('analysis', '') or ''
                h['hist_strategy'] = ai_obj.get('investment_strategy', '') or ''
                h['hist_trade'] = ai_obj.get('trade_strategy', {}) or {}
            except Exception:
                pass
        parsed_history.append(h)
    item['analysis_history'] = parsed_history

    # 6. 投资笔记
    notes = WatchlistDAO().list_notes(code, limit=10)
    item['notes'] = notes

    # 7. 投资论文（论点+假设+红线+卖出条件）
    from src.models.database import WatchlistThesisDAO
    item['thesis'] = WatchlistThesisDAO.get(code)

    return item


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

    # 行业均值参照（P1② 评分透明化收尾）：本股板块均值，只读展示
    sector_avg = None
    _sector = snapshot.get("sector")
    if _sector:
        _row = StockSnapshotDAO.get_sector_averages().get(_sector)
        if _row:
            sector_avg = {"sector": _sector, **_row}

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
        "sector_avg": sector_avg,
        "ai_parsed": ai_parsed,
        "trade_parsed": trade_parsed,
        "financial_summary": financial_summary,
        "annual_reports": annual_reports,
        "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    })


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


@app.get("/journal/compare/{journal_date1}/{journal_date2}", response_class=HTMLResponse)
async def journal_compare(request: Request, journal_date1: str, journal_date2: str):
    """历史笔记对比页"""
    journal1 = AiJournalDAO().get_by_date(journal_date1)
    journal2 = AiJournalDAO().get_by_date(journal_date2)
    history_list = AiJournalDAO().list_all()
    config = load_config()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')
    
    return templates.TemplateResponse(request, "journal_compare.html", {
        "request": request,
        "page_title": page_title,
        "journal1": journal1,
        "journal2": journal2,
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
    """指定日期笔记 JSON（缺失 404，与 /latest 一致）"""
    journal = AiJournalDAO().get_by_date(journal_date)
    if not journal:
        raise HTTPException(status_code=404, detail="Journal not found")
    return journal


# 注：已移除路由的注释尸体于 2026-09-21 清理（git 历史可查）；B5 漂移逻辑见下方 live 的 _detect_pool_drift。


def _detect_pool_drift(current_json: dict, previous_json: dict) -> list[dict]:
    """B5 论点漂移（实时计算）：打脸回归 + 池内股跨轮 Signal/verdict 翻转。
    输入为两期 actions_summary 解析后的 dict；screening 跨轮取最近两轮。"""
    drifts = []
    cur_d = (current_json.get('details') or {}) if current_json else {}
    prev_d = (previous_json.get('details') or {}) if previous_json else {}
    cur_add = {i.get('code') for i in cur_d.get('add', []) if i.get('code')}
    prev_out = {i.get('code') for i in prev_d.get('remove', []) if i.get('code')}
    for code in sorted(cur_add & prev_out):
        drifts.append({
            "type": "re_entry",
            "message": f"打脸回归：{code} 上期调出本期调回，检查当初调出理由是否站得住",
            "severity": "warning",
        })
    # 跨轮 Signal/verdict 翻转（池内相关股）
    pool_codes = set()
    for d in (cur_d, prev_d):
        for a in ('add', 'remove', 'keep', 'watch'):
            for i in d.get(a, []):
                if i.get('code'):
                    pool_codes.add(i.get('code'))
    if not pool_codes:
        return drifts
    run_ids = _latest_two_run_ids()
    if len(run_ids) < 2:
        return drifts
    prev_rows = {r['code']: r for r in
                 ScreeningResultDAO().get_results_for_run(run_ids[1])}
    cur_rows = {r['code']: r for r in
                ScreeningResultDAO().get_results_for_run(run_ids[0])}
    for code in sorted(pool_codes):
        p, c = prev_rows.get(code), cur_rows.get(code)
        if not p or not c:
            continue
        ps = _trade_signal(p)
        cs = _trade_signal(c)
        if ps and cs and ps != cs:
            drifts.append({
                "type": "signal_flip",
                "message": f"Signal 反转：{code} 上轮 {ps} → 本轮 {cs}",
                "severity": "warning",
            })
        pv, cv = _ai_verdict(p), _ai_verdict(c)
        if pv and cv and pv != cv:
            drifts.append({
                "type": "verdict_flip",
                "message": f"结论反转：{code} 上轮 {pv} → 本轮 {cv}",
                "severity": "warning",
            })
    return drifts


def _latest_two_run_ids() -> list[str]:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT run_id FROM screening_result "
            "ORDER BY run_date DESC, run_id DESC LIMIT 2").fetchall()
    return [r[0] for r in rows]


def _trade_signal(row: dict):
    try:
        t = row.get('ai_trade_strategy')
        return (json.loads(t) if isinstance(t, str) else t or {}).get('signal')
    except Exception:
        return None


def _ai_verdict(row: dict):
    try:
        a = row.get('ai_analysis')
        return (json.loads(a) if isinstance(a, str) else a or {}).get('verdict')
    except Exception:
        return None


@app.get("/api/journal/{journal_date}/conflicts")
async def api_journal_conflicts(journal_date: str):
    """笔记矛盾检测分析"""
    journal = AiJournalDAO().get_by_date(journal_date)
    if not journal:
        return {"error": "Journal not found"}
    
    previous = AiJournalDAO().get_previous(journal_date)
    
    if not previous:
        return {"has_conflicts": False, "message": "第一期笔记，无可对比"}
    
    # 简单的矛盾检测逻辑
    conflicts = []
    
    # 检查标题变化
    if journal['title'] != previous['title']:
        conflicts.append({
            "type": "title_change",
            "message": f"标题从 '{previous['title']}' 变为 '{journal['title']}'",
            "severity": "info"
        })
    
    # 检查内容长度变化
    content_length_change = len(journal['content_md']) - len(previous['content_md'])
    if abs(content_length_change) > 500:
        direction = "增加" if content_length_change > 0 else "减少"
        conflicts.append({
            "type": "content_length_change",
            "message": f"内容长度{direction} {abs(content_length_change)} 字符",
            "severity": "info"
        })
    
    # 检查模型变化
    current_actions = journal['actions_summary']
    previous_actions = previous['actions_summary']
    
    if current_actions and previous_actions:
        try:
            current_json = json.loads(current_actions)
            previous_json = json.loads(previous_actions)
            
            if current_json.get('model') != previous_json.get('model'):
                conflicts.append({
                    "type": "model_change",
                    "message": f"分析模型从 {previous_json.get('model', '未知')} 变为 {current_json.get('model', '未知')}",
                    "severity": "info"
                })
        except:
            pass
    
    # 检查市场环境变化
    if journal['market_snapshot'] != previous['market_snapshot']:
        conflicts.append({
            "type": "market_change",
            "message": "市场环境发生变化",
            "severity": "info"
        })

    # B5 论点漂移：打脸回归 + 池内股跨轮 Signal/verdict 翻转（实时计算，免新表）
    try:
        conflicts.extend(_detect_pool_drift(current_json, previous_json))
    except Exception:
        pass

    return {
        "has_conflicts": len(conflicts) > 0,
        "conflicts": conflicts,
        "previous_date": previous['journal_date'],
        "current_date": journal['journal_date']
    }


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
