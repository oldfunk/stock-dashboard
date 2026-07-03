#!/usr/bin/env python3
"""Run the full stock dashboard pipeline — data then AI, sequential."""
import logging, sys, time, subprocess, json, os, importlib

logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True,
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from datetime import datetime
import yaml
from src.models.database import (get_connection, RunLogDAO, MarketIndexDAO,
                                 init_database, PipelineProgressDAO)

init_database()
cfg = yaml.safe_load(open('config/config.yaml'))
progress = PipelineProgressDAO()

# ── 1. 采集行情 ──
from src.collector.akshare_fetcher import fetch_all_stocks_basic
records = fetch_all_stocks_basic()
total = len(records)
run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
logger.info(f'全A股: {total} 只')
# 先初始化进度（现在有实际total了）
progress.init_run(run_id, 'collecting', f'采集全A股 {total} 只', total=total, ai_total=20)
progress.update(run_id, 'collecting', f'采集完成 {total} 只', processed=total, total=total)
progress.update(run_id, 'screening', f'初筛 {total} 只...', processed=total, total=total, ai_total=0)

# ── 2. 初筛 ──
from src.collector.akshare_fetcher import pre_filter_stocks
candidates = pre_filter_stocks(records, cfg)
progress.update(run_id, 'enriching', f'获取 {len(candidates)} 只财务数据...')
RunLogDAO().start_run(run_id)
logger.info(f'初筛流通: {len(candidates)}')

# ── 3. 财务补充 ──
from src.collector.akshare_fetcher import enrich_financial_data
enrich_financial_data(candidates)
roe_ok = sum(1 for c in candidates if c.get('roe') is not None)
logger.info(f'ROE: {roe_ok}/{len(candidates)}')

# ── 4. 筛选 ──
run_date = datetime.now().strftime('%Y-%m-%d')
from src.screener.value_screener import run_screener
top = run_screener(cfg, candidates, run_id, run_date)
n_top = len(top)
logger.info(f'筛选: {n_top} 只')
for s in top[:10]:
    logger.info(f'  score={s["score"]} {s["code"]} {s["name"]:10s} PE={s.get("pe")} ROE={s.get("roe")}%')
progress.update(run_id, 'screened', f'筛选完成 {n_top} 只', processed=n_top, ai_total=n_top)

# ── 5. 大盘指数 ──
time.sleep(1)
try:
    raw = subprocess.run(['curl','-s','--connect-timeout','10',
        'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.000001,0.399001,0.399006,1.000688&fields=f2,f3,f4,f12,f14'],
        capture_output=True, text=True, timeout=15).stdout
    if raw and len(raw) > 10:
        d = json.loads(raw)
        items = d.get('data',{}).get('diff',[])
        if items:
            names = {'1.000001':'上证指数','0.399001':'深证成指','0.399006':'创业板指','1.000688':'科创50'}
            idx = []
            for item in items:
                sid = item.get('f12','')
                mv = item.get('f2',0) or 0
                idx.append({'index_code':sid,'index_name':names.get(sid,sid),
                    'current_value':float(mv),'change_percent':item.get('f3'),
                    'change_amount':item.get('f4'),'volume':0,'amount':0,
                    'pe':None,'pb':None,'timestamp':datetime.now().isoformat(),'date':datetime.now().strftime('%Y-%m-%d')})
            MarketIndexDAO().save(idx)
            for i in idx:
                logger.info(f'  大盘: {i["index_name"]} {i["current_value"]} ({i["change_percent"]:+.2f}%)')
    else:
        logger.warning('大盘指数接口返回空')
except Exception as idx_err:
    logger.warning(f'大盘指数获取失败（不影响后续）: {idx_err}')

# ── 6. AI 分析（顺序执行，采集完后才启动）──
if not top:
    logger.warning('无入选股票，跳过AI分析')
    RunLogDAO().complete_run(run_id, total, n_top, 0)
    progress.update(run_id, 'done', '无股票可分析')
    sys.exit(0)

progress.update(run_id, 'analyzing', f'AI分析 0/{n_top}...', ai_total=n_top, ai_done=0, ai_failed=0)
logger.info(f'开始AI分析 {n_top} 只...')

# 动态导入 run_ai_analysis
from src.models.database import StockAnalysisHistoryDAO, ScreeningResultDAO
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJ, '.env'), override=True)
import httpx, re

api_key = os.getenv('STOCK_AI_API_KEY')
api_url = os.getenv('STOCK_AI_API_BASE', 'https://open.bigmodel.cn/api/paas/v4/chat/completions')
model_name = os.getenv('STOCK_AI_MODEL', 'glm-4.7-flash')
headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
BACKOFF_SCHEDULE = [30, 60, 120, 240, 300, 300, 300, 300, 300, 300,
                     300, 300, 300, 300, 300, 300, 300, 300, 300, 300]
PROMPT_TEMPLATE = """Analyze A-share stock {name}({code}) for value investing.
PE={pe}, PB={pb}, ROE={roe}%,
Revenue growth={revenue_growth}%, Profit growth={profit_growth}%,
Debt ratio={debt_ratio}%, Market cap={market_cap}B.
Reason for selection: {reason}

Output JSON with: "analysis", "investment_strategy", "trade_strategy"(with buy_zone, target_price, stop_loss, take_profit)."""

history_dao = StockAnalysisHistoryDAO()
screening_dao = ScreeningResultDAO()
analyzed_ok = 0
analyzed_failed = 0

for idx, stock in enumerate(top):
    code, name = stock['code'], stock['name']
    logger.info(f'[{idx+1}/{n_top}] {code} {name} (score={stock["score"]})...')
    progress.update(run_id, stage_label=f'AI分析 {idx}/{n_top}...', ai_done=analyzed_ok)

    prompt = PROMPT_TEMPLATE.format(
        name=name, code=code,
        pe=stock.get('pe', 'N/A'), pb=stock.get('pb', 'N/A'),
        roe=stock.get('roe', 'N/A'),
        revenue_growth=stock.get('revenue_growth', 'N/A'),
        profit_growth=stock.get('profit_growth', 'N/A'),
        debt_ratio=stock.get('debt_ratio', 'N/A'),
        market_cap=stock.get('market_cap', 'N/A'),
        reason=stock.get('reason', ''),
    )

    # API call with retries
    result = None
    payload = {'model': model_name, 'messages': [
        {'role': 'system', 'content': '你是专业的A股价值投资分析师。请严格按JSON格式输出。'},
        {'role': 'user', 'content': prompt}
    ], 'temperature': 0.3, 'max_tokens': 2000}
    max_retries = len(BACKOFF_SCHEDULE)

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=120) as c:
                r = c.post(api_url, headers=headers, json=payload)
                if r.status_code == 200:
                    content = r.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                    if not content or len(content) < 10:
                        logger.warning(f'  模型返回内容太短({len(content)} chars), retrying...')
                        time.sleep(BACKOFF_SCHEDULE[attempt])
                        continue
                    # 尝试多种JSON提取方式
                    result = None
                    # 1) ```json ... ``` 代码块
                    jmatch = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
                    if jmatch:
                        try:
                            result = json.loads(jmatch.group(1).strip())
                        except json.JSONDecodeError:
                            pass
                    # 2) 第一个 { 到最后一个 }
                    if not result:
                        jmatch = re.search(r'\{.*\}', content, re.DOTALL)
                        if jmatch:
                            try:
                                result = json.loads(jmatch.group())
                            except json.JSONDecodeError:
                                pass
                    # 3) 直接整段解析
                    if not result:
                        try:
                            result = json.loads(content.strip())
                        except json.JSONDecodeError:
                            pass
                    if result:
                        break
                    logger.warning(f'  200响应但无法解析JSON, retrying...')
                    time.sleep(BACKOFF_SCHEDULE[attempt])
                elif r.status_code == 429:
                    wait = BACKOFF_SCHEDULE[attempt]
                    logger.warning(f'  429, 退避 {wait}s (attempt {attempt+1}/{max_retries})')
                    time.sleep(wait)
                elif r.status_code == 503:
                    time.sleep(BACKOFF_SCHEDULE[attempt])
                else:
                    logger.warning(f'  HTTP {r.status_code}, retrying...')
                    time.sleep(BACKOFF_SCHEDULE[attempt])
        except httpx.TimeoutException:
            logger.warning(f'  Timeout, backoff {BACKOFF_SCHEDULE[attempt]}s...')
            time.sleep(BACKOFF_SCHEDULE[attempt])
        except Exception as e:
            logger.warning(f'  Error: {e}, backoff {BACKOFF_SCHEDULE[attempt]}s...')
            time.sleep(BACKOFF_SCHEDULE[attempt])

    if not result:
        logger.warning(f'  ✗ Failed')
        history_dao.save(code, run_id, stock.get('score'), '{}', '{}')
        analyzed_failed += 1
        progress.update(run_id, ai_done=analyzed_ok, ai_failed=analyzed_failed)
        continue

    analysis_json = json.dumps(result, ensure_ascii=False)
    strategy = result.get('investment_strategy', '')
    if isinstance(strategy, dict):
        strategy = json.dumps(strategy, ensure_ascii=False)
    trade = result.get('trade_strategy', {})
    trade_json = json.dumps(trade, ensure_ascii=False) if isinstance(trade, dict) else str(trade)
    history_dao.save(code, run_id, stock.get('score'), analysis_json, trade_json)
    screening_dao.update_ai_analysis(run_id, code, analysis_json, strategy, trade_json)
    analyzed_ok += 1
    logger.info(f'  ✅ {analyzed_ok}/{n_top}')
    progress.update(run_id, ai_done=analyzed_ok)

    # 股票间隔 60s
    if idx < len(top) - 1:
        logger.info('  冷却 60s...')
        time.sleep(60)

# ── 完成 ──
RunLogDAO().complete_run(run_id, total, n_top, analyzed_ok)
progress.update(run_id, 'done', f'完成: 筛选{n_top}只, AI分析{analyzed_ok}只')
logger.info(f'流水线完成: 全A股{total}只 → 筛选{n_top}只 → AI分析{analyzed_ok}只（失败{analyzed_failed}只）')
