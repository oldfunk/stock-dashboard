"""
Web 看板 - FastAPI 路由
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import yaml

from src.models.database import (
    init_database,
    MarketIndexDAO,
    ScreeningResultDAO,
    RunLogDAO,
)

logger = logging.getLogger(__name__)

# -------- 创建 FastAPI 应用 --------
app = FastAPI(title="价值投资选股看板")

# -------- 模板和静态文件 --------
templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(templates_dir))

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def load_config_safe():
    """安全加载配置"""
    config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
    try:
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


# -------- 路由 --------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """看板首页"""
    config = load_config_safe()
    page_title = config.get('web', {}).get('page_title', '价值投资选股看板')

    # 获取最新大盘数据
    market_dao = MarketIndexDAO()
    indices = market_dao.get_latest()

    # 获取最新筛选结果
    result_dao = ScreeningResultDAO()
    stocks = result_dao.get_latest_results()

    # 获取运行状态
    run_log = RunLogDAO().get_latest_run()

    # 解析 AI 分析 JSON
    for stock in stocks:
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

    refresh = config.get('web', {}).get('refresh_interval', 60)

    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "page_title": page_title,
        "indices": indices,
        "stocks": stocks,
        "run_log": run_log,
        "refresh_interval": refresh,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.get("/api/indices")
async def api_indices():
    """大盘数据 API"""
    indices = MarketIndexDAO().get_latest()
    return [dict(i) for i in indices]


@app.get("/api/stocks")
async def api_stocks():
    """最新选股结果 API"""
    stocks = ScreeningResultDAO().get_latest_results()
    for s in stocks:
        for key in ['ai_analysis', 'ai_trade_strategy']:
            if s.get(key):
                try:
                    s[key] = json.loads(s[key])
                except (json.JSONDecodeError, TypeError):
                    pass
    return [dict(s) for s in stocks]


@app.get("/api/status")
async def api_status():
    """运行状态 API"""
    run = RunLogDAO().get_latest_run()
    return dict(run) if run else {"status": "no_runs"}


@app.post("/api/trigger_update")
async def trigger_update():
    """手动触发每日更新"""
    from src.orchestrator import run_daily_pipeline, load_config
    config = load_config()
    import threading
    thread = threading.Thread(target=run_daily_pipeline, args=(config,), daemon=True)
    thread.start()
    return {"status": "started", "message": "更新流程已启动，请在日志中查看进度"}


def run_server():
    """启动 Web 服务"""
    config = load_config_safe()
    host = config.get('web', {}).get('host', '0.0.0.0')
    port = config.get('web', {}).get('port', 9527)

    # 确保数据库已初始化
    init_database()

    import uvicorn
    print(f"Stock Dashboard running at http://{host}:{port}")
    print(f"Press Ctrl+C to stop")
    uvicorn.run(app, host=host, port=port, log_level="info")
