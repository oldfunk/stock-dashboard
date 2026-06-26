#!/usr/bin/env python3
"""Run the full stock dashboard pipeline."""
import logging, sys, time, subprocess, json, os
logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True,
                    format='%(asctime)s [%(levelname)s] %(message)s')

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from collector.akshare_fetcher import pre_filter_stocks, enrich_financial_data
from models.database import get_connection, MarketIndexDAO, RunLogDAO, init_database
from screener.value_screener import run_screener
from datetime import datetime
import yaml

init_database()
cfg = yaml.safe_load(open('config/config.yaml'))

conn = get_connection()
rows = conn.execute('SELECT * FROM stock_snapshot').fetchall()
records = [dict(r) for r in rows]
print(f'DB: {len(records)} stocks')

candidates = pre_filter_stocks(records, cfg)
print(f'初筛: {len(candidates)}')

enrich_financial_data(candidates)
roe_ok = sum(1 for c in candidates if c.get('roe') is not None)
print(f'ROE: {roe_ok}/{len(candidates)}')

run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
run_date = datetime.now().strftime('%Y-%m-%d')
RunLogDAO().start_run(run_id)
top = run_screener(cfg, candidates, run_id, run_date)
print(f'筛选: {len(top)} 只')
for s in top[:10]:
    print(f'  score={s["score"]} {s["code"]} {s["name"]:10s} PE={s.get("pe")} ROE={s.get("roe")}%  {s.get("reason","")[:60]}')

time.sleep(1)
raw = subprocess.run(['curl','-s','--connect-timeout','10',
    'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.000001,0.399001,0.399006,1.000688&fields=f2,f3,f4,f12,f14'],
    capture_output=True, text=True, timeout=15).stdout
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
        print(f'  大盘: {i["index_name"]} {i["current_value"]} ({i["change_percent"]:+.2f}%)')
print('完成！')
