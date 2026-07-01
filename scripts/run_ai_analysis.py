#!/usr/bin/env python3
"""
慢喂 AI 分析 — 按 cron 节奏跑，每次分析 1 只。

用法：
  python3 scripts/run_ai_analysis.py              # 分析最新批次中优先级最高的未分析股票
  python3 scripts/run_ai_analysis.py --once       # 同上，分析 1 只
  python3 scripts/run_ai_analysis.py --stale=3    # 分析 3 天未更新的股票（含已分析过的）
  python3 scripts/run_ai_analysis.py --all        # 一次全跑完（不推荐，可能限流）

退出码:
  0 = 成功分析了 1 只
  1 = 没有需要分析的股票（全部完成）
  2 = API 错误
"""
import sys, os, json, time, re, httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)

from src.models.database import (
    ScreeningResultDAO, StockAnalysisHistoryDAO, get_connection, RunLogDAO
)

# ── 解析参数 ──
analyze_all = '--all' in sys.argv
stale_days = 3
for a in sys.argv:
    if a.startswith('--stale='):
        stale_days = int(a.split('=')[1])

# ── 配置 ──
api_key = os.getenv('STOCK_AI_API_KEY')
if not api_key:
    print("[ERROR] STOCK_AI_API_KEY not set in .env")
    sys.exit(2)

api_url = os.getenv('STOCK_AI_API_BASE', 'https://opencode.ai/zen/v1/chat/completions')
model = os.getenv('STOCK_AI_MODEL', 'deepseek-v4-flash-free')
headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}

# ── 获取最新批次 ──
conn = get_connection()
row = conn.execute(
    "SELECT run_id FROM run_log WHERE status='completed' ORDER BY start_time DESC LIMIT 1"
).fetchone()
conn.close()
if not row:
    print("[SKIP] No completed runs found")
    sys.exit(1)

run_id = row['run_id']
print(f"Latest run: {run_id}")

# ── 获取候选池 ──
conn = get_connection()
rows = conn.execute("""
    SELECT * FROM screening_result WHERE run_id = ? ORDER BY score DESC
""", (run_id,)).fetchall()
conn.close()

if not rows:
    print("[SKIP] No stocks in latest run")
    sys.exit(1)

stocks = [dict(r) for r in rows]

# ── 确定需要分析的股票 ──
pending = []
for s in stocks:
    code = s['code']
    has_analysis = bool((s.get('ai_analysis') or '').strip())

    if has_analysis and not analyze_all:
        latest = StockAnalysisHistoryDAO().get_latest_for_code(code)
        if latest and latest.get('ai_analysis') and latest['ai_analysis'] != '{}':
            from datetime import datetime
            age = (datetime.now() - datetime.fromisoformat(latest['created_at'])).days
            if age < stale_days:
                continue  # 足够新鲜，跳过
        # 有分析但已过期，重新分析
    else:
        # 无分析：检查今天是否已经尝试过但失败了
        latest = StockAnalysisHistoryDAO().get_latest_for_code(code)
        if latest and latest.get('ai_analysis') == '{}':
            from datetime import datetime
            attempted_today = (datetime.now() - datetime.fromisoformat(latest['created_at'])).total_seconds() < 86400
            if attempted_today:
                continue  # 今天已试过且失败了，跳过去试下一只

    pending.append(s)

if not pending:
    print(f"[DONE] All {len(stocks)} stocks have recent analysis (stale={stale_days}d)")
    sys.exit(1)

# ── 决定本次分析几只 ──
targets = pending if analyze_all else pending[:1]
print(f"Pending: {len(pending)}, analyzing this run: {len(targets)}")

# ── Prompt ──
PROMPT_TEMPLATE = """Analyze A-share stock {name}({code}) for value investing.
PE={pe}, PB={pb}, ROE={roe}%,
Revenue growth={revenue_growth}%, Profit growth={profit_growth}%,
Debt ratio={debt_ratio}%, Market cap={market_cap}B.
Reason for selection: {reason}

Output JSON with: "analysis", "investment_strategy", "trade_strategy"(with buy_zone, target_price, stop_loss, take_profit)."""

# ── API call ──
def call_api(prompt):
    payload = {'model': model, 'messages': [
        {'role': 'system', 'content': '你是专业价值投资分析师。输出严格JSON。'},
        {'role': 'user', 'content': prompt}
    ], 'temperature': 0.3, 'max_tokens': 2000}

    for attempt in range(10):
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post(api_url, headers=headers, json=payload)
                if r.status_code == 200:
                    content = r.json()['choices'][0]['message']['content']
                    # 尝试多种 JSON 提取策略
                    jmatch = re.search(r'\{.*\}', content, re.DOTALL)
                    if jmatch:
                        try:
                            return json.loads(jmatch.group())
                        except json.JSONDecodeError:
                            pass
                    # 尝试修复截断：补上缺失的 }
                    cleaned = content.strip()
                    if cleaned.startswith('{') and not cleaned.endswith('}'):
                        cleaned += '}'
                        try:
                            return json.loads(cleaned)
                        except json.JSONDecodeError:
                            pass
                    # 未解析成功，重试
                    print(f"  Attempt {attempt+1}: bad JSON ({len(content)} chars), retrying...")
                    time.sleep(30)
                    continue
                elif r.status_code == 429:
                    wait = 120
                    print(f"  429, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  HTTP {r.status_code}, retrying...")
                    time.sleep(60)
        except Exception as e:
            print(f"  Error: {e}")
            if attempt < 9:
                time.sleep(30)
    return None

# ── 逐一分析 ──
history_dao = StockAnalysisHistoryDAO()
screening_dao = ScreeningResultDAO()
analyzed_ok = 0

for stock in targets:
    code, name = stock['code'], stock['name']
    print(f"\n[{code}] {name} (score={stock['score']})...")

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

    result = call_api(prompt)
    if not result:
        print(f"  ✗ Failed")
        # 记录失败尝试，避免下次重复试
        history_dao.save(code, run_id, stock.get('score'), '{}', '{}')
        if not analyze_all:
            sys.exit(2)
        continue

    analysis_json = json.dumps(result, ensure_ascii=False)
    strategy = result.get('investment_strategy', '')
    trade = result.get('trade_strategy', {})
    trade_json = json.dumps(trade, ensure_ascii=False) if isinstance(trade, dict) else str(trade)

    # 写入历史表（累积记录）
    history_dao.save(code, run_id, stock.get('score'), analysis_json, trade_json)

    # 更新 screening_result 当前视图
    screening_dao.update_ai_analysis(run_id, code, analysis_json, strategy, trade_json)

    print(f"  ✅ Saved to analysis history")
    analyzed_ok += 1

    # 非 --all 模式只分析 1 只就退出
    if not analyze_all:
        break

    # --all 模式间隔 60s
    if stock != targets[-1]:
        print("  Cooling 60s...")
        time.sleep(60)

# ── 输出摘要 ──
if analyze_all:
    remaining = len(pending) - analyzed_ok
    print(f"\nAnalyzed: {analyzed_ok}, remaining: {remaining}")
else:
    remaining = len(pending) - 1
    print(f"\nAnalyzed: {analyzed_ok} this run, {remaining} still pending")
    if remaining > 0:
        print(f"Next run will process: {targets[0]['code'] if analyzed_ok > 0 else pending[1]['code']}")

sys.exit(0 if analyzed_ok > 0 else 2)
