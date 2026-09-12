"""C2.5 bulk: fill roic/fcf for all stocks, then rebuild summaries."""
import sqlite3
import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from src.collector.akshare_fetcher import _fetch_eastmoney_roic_fcf, rebuild_financial_summaries

DB = 'data/db/stock_dashboard.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Phase 1: Fill roic/fcf from eastmoney
codes = [r[0] for r in conn.execute(
    'SELECT DISTINCT stock_code FROM financial_history WHERE roic IS NULL OR fcf IS NULL'
).fetchall()]
print(f'Phase 1: {len(codes)} stocks need roic/fcf')

filled_total = 0
updated = 0
for i, code in enumerate(codes):
    try:
        em_data = _fetch_eastmoney_roic_fcf(code)
        if not em_data:
            continue
        rows = conn.execute(
            'SELECT report_date, roic, fcf FROM financial_history WHERE stock_code = ?', (code,)
        ).fetchall()
        changes = 0
        for r in rows:
            rpt = r['report_date']
            if rpt in em_data:
                em = em_data[rpt]
                new_roic = r['roic'] if r['roic'] is not None else em.get('roic')
                new_fcf = r['fcf'] if r['fcf'] is not None else em.get('fcf')
                if new_roic != r['roic'] or new_fcf != r['fcf']:
                    conn.execute(
                        'UPDATE financial_history SET roic=?, fcf=? WHERE stock_code=? AND report_date=?',
                        (new_roic, new_fcf, code, rpt)
                    )
                    changes += 1
        if changes:
            filled_total += changes
            updated += 1
        if (i + 1) % 100 == 0:
            conn.commit()
            print(f'  {i+1}/{len(codes)} done, {updated} updated, {filled_total} fields')
        time.sleep(0.1)
    except Exception:
        pass

conn.commit()
print(f'Phase 1 done: {updated} stocks updated, {filled_total} fields filled')

# Phase 2: Rebuild financial summaries
all_codes = [{'code': r[0]} for r in conn.execute(
    'SELECT DISTINCT stock_code FROM financial_history'
).fetchall()]
print(f'\nPhase 2: Rebuilding summaries for {len(all_codes)} stocks...')
rebuild_financial_summaries(all_codes)

# Verify
r = conn.execute(
    'SELECT count(*) as total, sum(case when roic_10y_avg IS NOT NULL then 1 else 0 end) as has_roic, '
    'sum(case when fcf_5y_sum IS NOT NULL then 1 else 0 end) as has_fcf FROM financial_summary'
).fetchone()
print(f'\nVerify: {r[0]} summaries, {r[1]} with roic_10y_avg, {r[2]} with fcf_5y_sum')
conn.close()
