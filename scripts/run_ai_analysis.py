#!/usr/bin/env python3
"""Background AI analysis with patience for free tier rate limits.
Runs one stock at a time with 120s cooldown between attempts."""
import sys, os, json, time, re, httpx
sys.path.insert(0, '/home/debian/stock-dashboard')

from dotenv import load_dotenv
load_dotenv('/home/debian/stock-dashboard/.env', override=True)
from src.models.database import ScreeningResultDAO, get_connection, RunLogDAO

api_key = os.getenv('STOCK_AI_API_KEY')
headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
dao = ScreeningResultDAO()

# Get stocks that need AI analysis
stocks = dao.get_latest_results()
print(f'Need AI analysis for {len(stocks)} stocks')

analyzed = 0
for stock in stocks:
    if stock.get('ai_analysis'):
        analyzed += 1
        continue
    
    code, name = stock['code'], stock['name']
    print(f'\n[{code}] {name}...')
    
    prompt = f'''Analyze A-share stock {name}({code}) for value investing.
PE={stock.get("pe")}, PB={stock.get("pb")}, ROE={stock.get("roe")}%,
Revenue growth={stock.get("revenue_growth")}%, Profit growth={stock.get("profit_growth")}%,
Debt ratio={stock.get("debt_ratio")}%, Market cap={stock.get("market_cap")}B.
Reason for selection: {stock.get("reason", "")}

Output JSON with: "analysis", "investment_strategy", "trade_strategy"(with buy_zone, target_price, stop_loss, take_profit).'''

    payload = {'model': 'deepseek-v4-flash-free', 'messages': [
        {'role': 'system', 'content': '你是专业价值投资分析师。输出严格JSON。'},
        {'role': 'user', 'content': prompt}
    ], 'temperature': 0.3, 'max_tokens': 2000}

    success = False
    for attempt in range(10):
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post('https://opencode.ai/zen/v1/chat/completions', headers=headers, json=payload)
                if r.status_code == 200:
                    content = r.json()['choices'][0]['message']['content']
                    jmatch = re.search(r'\{.*\}', content, re.DOTALL)
                    if jmatch:
                        result = json.loads(jmatch.group())
                        analysis_json = json.dumps(result, ensure_ascii=False)
                        strategy = result.get('investment_strategy', '')
                        trade = result.get('trade_strategy', {})
                        trade_json = json.dumps(trade, ensure_ascii=False) if isinstance(trade, dict) else str(trade)
                        dao.update_ai_analysis('20260626_161430', code, analysis_json, strategy, trade_json)
                        print(f'  ✅ Done')
                        success = True
                    break
                elif r.status_code == 429:
                    wait = 120
                    print(f'  429, waiting {wait}s...')
                    time.sleep(wait)
                else:
                    print(f'  Error {r.status_code}')
                    break
        except Exception as e:
            print(f'  Error: {e}')
            time.sleep(30)
    
    if not success:
        print(f'  Skipped after retries')
    
    # Cooldown between stocks
    if not success:
        time.sleep(60)

print(f'\nDone! {analyzed} already had AI, processed others.')
