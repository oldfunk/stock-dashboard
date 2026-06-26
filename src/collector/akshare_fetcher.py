"""
A 股数据采集模块
数据源：腾讯全量行情API（含PE/PB/市值，通过curl绕过TUN阻断）
首次运行时用AKShare获取股票代码表并缓存，
后续运行直接读缓存+腾讯批查。
"""

import subprocess
import json
import time
import logging
import os
import re
import concurrent.futures
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(__file__), '../../data/cache/stock_codes.json')

def _safe_float(val) -> Optional[float]:
    if val is None: return None
    try:
        v = float(val)
        import math
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 2)
    except (ValueError, TypeError):
        return None

def _curl_get(url: str, timeout=30) -> Optional[str]:
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', '8',
             '--max-time', str(timeout), url],
            capture_output=True, timeout=timeout + 10)
        if r.returncode == 0 and r.stdout:
            try: return r.stdout.decode('utf-8')
            except UnicodeDecodeError: return r.stdout.decode('gbk', errors='replace')
    except Exception:
        pass
    return None

# ── 股票代码缓存 ──

def _get_stock_codes() -> list[dict]:
    """获取股票代码列表：优先读缓存，否则用AKShare生成"""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)

    # 尝试读缓存
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                codes = json.load(f)
            if len(codes) > 5000:
                logger.info(f"[代码表] 从缓存读取 {len(codes)} 只")
                return codes
        except Exception:
            pass

    # 缓存不足，用AKShare生成
    logger.info("[代码表] 缓存不足，从新浪获取股票列表（首次较慢约70s）...")
    import akshare as ak
    try:
        df = ak.stock_zh_a_spot()
        codes = []
        for _, r in df.iterrows():
            code = str(r.get('代码', '')).strip()
            name = str(r.get('名称', '')).strip()
            m = re.search(r'(\d{6})', code)
            if m and name:
                codes.append({'code': m.group(1), 'name': name})
    except Exception as e:
        logger.error(f"[代码表] AKShare失败: {e}")
        return []

    logger.info(f"[代码表] 获取 {len(codes)} 只")
    with open(CACHE_FILE, 'w') as f:
        json.dump(codes, f, ensure_ascii=False)
    return codes

# ── 腾讯批量行情 ──

def _tc_encode(code: str) -> str:
    c = code.strip()
    if c.startswith('6') or c.startswith('9'):
        return f"sh{c}"
    return f"sz{c}"

def _parse_tc_line(raw: str) -> Optional[dict]:
    try:
        parts = raw.split('~')
        if len(parts) < 50: return None
        code = parts[2].strip()
        return {
            'code': code,
            'name': parts[1].strip(),
            'current_price': _safe_float(parts[3]),
            'pe': _safe_float(parts[39]) if len(parts) > 39 else None,
            'pb': _safe_float(parts[46]) if len(parts) > 46 else None,
            'market_cap': _safe_float(parts[45]) if len(parts) > 45 else None,
            'change_percent': _safe_float(parts[32]) if len(parts) > 32 else None,
        }
    except Exception:
        return None

def fetch_tencent_batch(codes: list[str], batch_size=80) -> list[dict]:
    """从腾讯API批量获取股票行情"""
    results, tc_codes = [], [_tc_encode(c) for c in codes]
    for i in range(0, len(tc_codes), batch_size):
        batch = tc_codes[i:i + batch_size]
        raw = _curl_get(f"https://qt.gtimg.cn/q={','.join(batch)}", timeout=30)
        if raw:
            for line in raw.strip().split('\n'):
                if '=' not in line: continue
                line = line.split(';')[0]
                eq = line.find('=')
                if eq < 0: continue
                val = line[eq + 1:].strip().strip('"')
                q = _parse_tc_line(val)
                if q: results.append(q)
        if i + batch_size < len(tc_codes):
            time.sleep(0.5)
    logger.info(f"[腾讯] {len(results)} 只")
    return results

# ── 大盘指数（新浪curl绕过TUN阻断）──

def fetch_market_index(max_retries=2) -> list[dict]:
    now = datetime.now()
    ds, ts = now.strftime("%Y-%m-%d"), now.isoformat()
    # 新浪指数 code → 名称映射
    targets = {
        'sh000001': '上证指数', 'sz399001': '深证成指',
        'sz399006': '创业板指', 'sh000688': '科创50',
    }
    codes = list(targets.keys())
    for att in range(max_retries):
        try:
            raw = _curl_get(
                f"https://hq.sinajs.cn/list={','.join(codes)}",
                timeout=15)
            if not raw:
                continue
            idx = []
            for line in raw.strip().split('\n'):
                if '=' not in line:
                    continue
                eq = line.index('=')
                key = line[:eq].replace('var hq_str_', '').strip()
                name = targets.get(key)
                if not name:
                    continue
                val = line[eq + 1:].strip().strip('"').strip(';')
                parts = val.split(',')
                if len(parts) < 32:
                    continue
                cur = _safe_float(parts[1])
                yes_close = _safe_float(parts[3])
                high = _safe_float(parts[4])
                low = _safe_float(parts[5])
                vol = _safe_float(parts[8])
                amount = _safe_float(parts[9])
                chg_pct = ((cur - yes_close) / yes_close * 100) if (cur and yes_close and yes_close != 0) else None
                chg_amt = (cur - yes_close) if (cur and yes_close) else None
                idx.append({
                    'index_code': key,
                    'index_name': name,
                    'current_value': cur or 0,
                    'change_percent': _safe_float(chg_pct),
                    'change_amount': _safe_float(chg_amt),
                    'volume': vol or 0,
                    'amount': amount or 0,
                    'pe': None, 'pb': None,
                    'timestamp': ts, 'date': ds,
                })
            if len(idx) >= 2:
                logger.info(f"[采集] 大盘: {len(idx)} 条")
                return idx
        except Exception as e:
            logger.warning(f"[采集] 大盘{att+1}次失败: {e}")
            if att < max_retries - 1:
                time.sleep(3)
    return []

# ── 全A股行情 ──

def fetch_all_stocks_basic() -> list[dict]:
    """全A股行情：腾讯批查 + 股票代码缓存"""
    from src.models.database import StockSnapshotDAO
    now = datetime.now()
    ds = now.strftime("%Y-%m-%d")

    # 1. 获取代码表
    code_list = _get_stock_codes()
    if not code_list:
        return []

    # 2. 腾讯批查行情
    codes = [c['code'] for c in code_list]
    quotes = fetch_tencent_batch(codes)
    qmap = {q['code']: q for q in quotes}

    # 3. 合并
    records = []
    for entry in code_list:
        code = entry['code']
        q = qmap.get(code, {})
        name = entry['name']
        records.append({
            'code': code, 'name': name.replace(' ', ''),
            'market': 'A', 'sector': None,
            'pe': q.get('pe'), 'pb': q.get('pb'), 'ps': None,
            'market_cap': q.get('market_cap'), 'circulating_cap': None,
            'roe': None, 'revenue': None, 'revenue_growth': None,
            'profit': None, 'profit_growth': None, 'debt_ratio': None,
            'dividend_yield': None, 'current_price': q.get('current_price'),
            'high_52w': None, 'low_52w': None,
            'is_st': 1 if ('ST' in name or '*ST' in name) else 0,
            'list_date': None, 'snapshot_date': ds,
        })

    with_pe = sum(1 for r in records if r['pe'] is not None)
    logger.info(f"[采集] 全A股 {len(records)} 只 (有PE: {with_pe})")
    StockSnapshotDAO().save_batch(records)
    return records

# ── 初筛 ──

def pre_filter_stocks(records: list[dict], config: dict) -> list[dict]:
    cfg = config.get('screener', {}).get('conditions', {})
    candidates = []
    for s in records:
        if cfg.get('exclude_st', True) and s['is_st']: continue
        pe = s.get('pe')
        if pe is None or pe < cfg.get('min_pe', 3) or pe > cfg.get('max_pe', 20): continue
        pb = s.get('pb')
        if pb is not None and pb > cfg.get('max_pb', 3.5): continue
        mc = s.get('market_cap')
        if mc is not None and (mc < cfg.get('min_market_cap', 30) or mc > cfg.get('max_market_cap', 50000)): continue
        candidates.append(s)
    logger.info(f"[初筛] {len(records)} -> {len(candidates)}")
    return candidates

# ── 财务补充 ──

FIN_INDICATORS = {
    'roe': '净资产收益率(ROE)',
    'debt_ratio': '资产负债率',
    'revenue_growth': '营业总收入增长率',
    'profit_growth': '归属母公司净利润增长率',
}

def enrich_financial_data(stocks: list[dict], workers=8) -> list[dict]:
    import akshare as ak
    logger.info(f"[财务] 获取 {len(stocks)} 只 ({workers}线程)...")
    def _fetch(s):
        try:
            df = ak.stock_financial_abstract(symbol=s['code'])
            cols = [c for c in df.columns if c not in ('选项', '指标')]
            if not cols: return s
            annual = [c for c in cols if c.endswith('1231')]
            latest_a = annual[0] if annual else cols[-1]
            latest_q = cols[0]
            fa, fq = {}, {}
            for _, r in df.iterrows():
                i = str(r.get('指标', ''))
                fa[i] = _safe_float(r.get(latest_a))
                fq[i] = _safe_float(r.get(latest_q))
            s['roe'] = fa.get('净资产收益率(ROE)')
            s['debt_ratio'] = fa.get('资产负债率')
            s['revenue_growth'] = fq.get('营业总收入增长率')
            s['profit_growth'] = fq.get('归属母公司净利润增长率')
        except Exception as e:
            logger.debug(f"[财务] {s['code']} 失败: {e}")
        return s
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for f in concurrent.futures.as_completed({ex.submit(_fetch, s): i for i, s in enumerate(stocks)}):
            done += 1
            if done % 20 == 0 or done == len(stocks):
                logger.info(f"[财务] {done}/{len(stocks)}")
    logger.info(f"[财务] ROE: {sum(1 for s in stocks if s.get('roe') is not None)}/{len(stocks)}")
    return stocks

# ── 采集流水线 ──

def run_collect_pipeline(config: dict) -> list[dict]:
    from src.models.database import MarketIndexDAO

    indices = fetch_market_index()
    if indices:
        MarketIndexDAO().save(indices)
        for i in indices:
            logger.info(f"  {i['index_name']}: {i['current_value']}")

    records = fetch_all_stocks_basic()
    if not records: return []

    candidates = pre_filter_stocks(records, config)
    if not candidates: return []

    enrich_financial_data(candidates)
    return candidates
