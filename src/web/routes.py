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

from src.config import load_config
from src.models.database import (
    init_database,
    MarketIndexDAO,
    ScreeningResultDAO,
    StockAnalysisHistoryDAO,
    RunLogDAO,
    PipelineProgressDAO,
    FinancialHistoryDAO,
    FinancialSummaryDAO,
)
from src.scheduler import (
    MarketScheduler, set_active_codes, get_realtime_cache, fetch_stock_realtime,
    _active_codes, _cache_lock, _realtime_cache,
)
from src.utils import now_cn

logger = logging.getLogger(__name__)

# 全局调度器
_scheduler = MarketScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：加载 .env / 配置日志 / 初始化 DB / 启动调度器；停止时关调度器。"""
    from dotenv import load_dotenv
    from src.logging_config import setup_logging
    setup_logging()
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
        logger.info(f"[Web] 加载 .env: {env_path}")
    init_database()
    _scheduler.start()
    logger.info("[Web] 服务启动完成")
    try:
        yield
    finally:
        _scheduler.stop()
        logger.info("[Web] 服务关闭")


# -------- 创建 FastAPI 应用 --------
app = FastAPI(title="价值投资选股看板", lifespan=lifespan)


# -------- 模板和静态文件 --------
templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(templates_dir))

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

    # 解析 AI 分析 JSON 并附加历史记录
    history_dao = StockAnalysisHistoryDAO()
    for stock in stocks:
        code = stock['code']
        if stock.get('ai_analysis'):
            try:
                stock['ai_parsed'] = json.loads(stock['ai_analysis'])
            except (json.JSONDecodeError, TypeError):
                stock['ai_parsed'] = None
        if stock.get('ai_trade_strategy'):
            try:
                stock['trade_parsed'] = json.loads(stock['ai_trade_strategy'])
            except (json.JSONDecodeError, TypeError):
                stock['trade_parsed'] = None

        # 附加历史分析摘要
        history = history_dao.get_history(code, limit=5)
        parsed_history = []
        for h in history:
            if h.get('ai_analysis') and h['ai_analysis'] not in ['{}', '']:
                try:
                    ai_obj = json.loads(h['ai_analysis'])
                    # 保留原 combined_analysis 用于兼容
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
                    # 新增：结构化字段供模板使用
                    h['hist_analysis'] = ai_obj.get('analysis', '')
                    h['hist_strategy'] = ai_obj.get('investment_strategy', '')
                    h['hist_risk'] = ai_obj.get('reverse_thinking', '')
                    h['hist_trade'] = ai_obj.get('trade_strategy', {})
                    h['hist_mirror'] = ai_obj.get('mirror_counts', '')
                except Exception:
                    h['combined_analysis'] = '--'
            else:
                h['combined_analysis'] = '--'
            parsed_history.append(h)
        stock['analysis_history'] = parsed_history

        # Mirror counts 传递到前端
        if stock.get('ai_parsed') and isinstance(stock['ai_parsed'], dict):
            mc = stock['ai_parsed'].get('mirror_counts')
            if mc is not None:
                # mc 可能是 "0,1,0,1,1 第N句过多转折" 这样的字符串
                # 提取数字部分求和，以便模板中做 > 2 的整数比较
                if isinstance(mc, str):
                    import re
                    nums = re.findall(r'\d+', mc.split()[0] if ' ' in mc else mc)
                    total = sum(int(n) for n in nums) if nums else 0
                    stock['mirror_counts'] = str(mc)  # 原文保留用于显示
                    stock['mirror_total'] = total     # 数值用于比较
                else:
                    stock['mirror_counts'] = str(mc)
                    stock['mirror_total'] = int(mc) if mc else 0

        # 注入财务历史汇总（用于前端显示历史趋势）
        try:
            fs = FinancialSummaryDAO().get(code)
            if fs:
                stock['_summary'] = {
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
                    # 10年拓展字段
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

    refresh = config.get('web', {}).get('refresh_interval', 30)

    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "page_title": page_title,
        "indices": indices,
        "stocks": stocks,
        "run_log": run_log,
        "refresh_interval": refresh,
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

        for key in ['ai_analysis', 'ai_trade_strategy']:
            if s.get(key):
                try:
                    s[key] = json.loads(s[key])
                except (json.JSONDecodeError, TypeError):
                    pass

    # 附加历史记录
    hist_dao = StockAnalysisHistoryDAO()
    for s in stocks:
        history = hist_dao.get_history(s['code'], limit=5)
        for h in history:
            if h.get('ai_analysis') and h['ai_analysis'] not in ['{}', '']:
                try:
                    ai_obj = json.loads(h['ai_analysis'])
                    parts = []
                    if ai_obj.get('analysis'):
                        parts.append(ai_obj['analysis'])
                    if ai_obj.get('investment_strategy'):
                        parts.append(ai_obj['investment_strategy'])
                    if ai_obj.get('trade_strategy'):
                        parts.append(str(ai_obj['trade_strategy']))
                    h['combined_analysis'] = '\n\n'.join(parts) if parts else '--'
                except Exception:
                    h['combined_analysis'] = '--'
            else:
                h['combined_analysis'] = '--'
        s['analysis_history'] = history

    return [dict(s) for s in stocks]


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


@app.post("/api/trigger_update")
async def trigger_update():
    """手动触发每日更新（后台线程；orchestrator 内部有锁防并发）"""
    from src.orchestrator import run_daily_pipeline
    import threading
    config = load_config()
    thread = threading.Thread(target=run_daily_pipeline, args=(config,), daemon=True)
    thread.start()
    return {"status": "started", "message": "更新流程已启动，请在日志中查看进度"}


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
