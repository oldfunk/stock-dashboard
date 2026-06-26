"""
A 股数据采集模块
数据源：东方财富行情API（通过 curl 子进程，绕过 TUN 代理 SSL 阻断）
策略：curl全量快照(含PE/PB/市值ROE) → 初筛 → 并行财务补充
"""

import subprocess
import json
import time
import logging
import concurrent.futures
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _safe_float(val) -> Optional[float]:
    if val is None: return None
    try:
        v = float(val)
        import math
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 2)
    except (ValueError, TypeError):
        return None


def _curl_get(url: str, timeout=15) -> Optional[str]:
    """系统 curl 发 GET（绕过 Python requests 在 TUN 下的 SSL 问题）"""
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', str(timeout),
             '--max-time', str(timeout + 5), url],
            capture_output=True, text=True, timeout=timeout + 10)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except Exception:
        pass
    return None


# 东方财富 API 字段映射
# f12=代码, f14=名称, f2=最新价, f9=动态市盈率
# f20=总市值, f23=市净率, f37=ROE, f38=营收增长率
# f39=净利润增长率, f40=资产负债率, f41=股息率
EM_FIELDS = "f12,f14,f2,f3,f4,f9,f20,f23,f37,f38,f39,f40,f41"
# A 股全部板块
EM_A_SHARES = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def _build_em_url(pn: int, pz: int = 200) -> str:
    return (f"https://push2.eastmoney.com/api/qt/clist/get"
            f"?pn={pn}&pz={pz}&po=1&np=1&fltt=2&invt=2"
            f"&fid=f12&fs={EM_A_SHARES}&fields={EM_FIELDS}")


def _parse_em(raw: str) -> list[dict]:
    try:
        d = json.loads(raw).get('data', {})
        return d.get('diff', []) if isinstance(d.get('diff'), list) else []
    except (json.JSONDecodeError, AttributeError):
        return []


# ── 大盘指数 ──

def fetch_market_index(max_retries=3) -> list[dict]:
    import akshare as ak
    now = datetime.now()
    ds = now.strftime("%Y-%m-%d")
    ts = now.isoformat()
    targets = {'上证指数', '深证成指', '创业板指', '科创50'}

    for att in range(max_retries):
        try:
            df = ak.stock_zh_index_spot_em()
            idx = []
            for _, r in df.iterrows():
                n = str(r.get('名称', ''))
                if n in targets:
                    idx.append({'index_code': str(r.get('代码', '')), 'index_name': n,
                                'current_value': _safe_float(r.get('最新价')) or 0,
                                'change_percent': _safe_float(r.get('涨跌幅')),
                                'change_amount': _safe_float(r.get('涨跌额')),
                                'volume': _safe_float(r.get('成交量')) or 0,
                                'amount': _safe_float(r.get('成交额')) or 0,
                                'pe': None, 'pb': None, 'timestamp': ts, 'date': ds})
            if len(idx) >= 2:
                logger.info(f"[采集] 大盘: {len(idx)} 条")
                return idx
        except Exception as e:
            logger.warning(f"[采集] 大盘第{att+1}次失败: {e}")
            if att < max_retries - 1:
                time.sleep(5)
    return []


# ── 全 A 股快照 ──

def fetch_all_stocks_basic() -> list[dict]:
    """curl → 东方财富, 取全A股含PE/PB/ROE"""
    now = datetime.now()
    ds = now.strftime("%Y-%m-%d")

    raw = _curl_get(_build_em_url(1, 1))
    if not raw:
        return []
    meta = json.loads(raw).get('data', {})
    total = meta.get('total', 5000)
    pz = 200
    pages = (total // pz) + 1
    logger.info(f"[采集] curl EM: ~{total}只, {pages}页×{pz}")

    all_items = []
    for pn in range(1, pages + 1):
        raw = _curl_get(_build_em_url(pn, pz))
        if not raw:
            logger.warning(f"[采集] 第{pn}页失败, 跳过")
            continue
        all_items.extend(_parse_em(raw))
        if pn % 5 == 0 or pn == pages:
            logger.info(f"[采集] 进度 {pn}/{pages} ({len(all_items)}只)")
        if pn < pages:
            time.sleep(0.3)

    records = []
    for item in all_items:
        code = str(item.get('f12', '')).zfill(6)
        name = str(item.get('f14', ''))
        mc = _safe_float(item.get('f20'))
        records.append({
            'code': code, 'name': name.replace(' ', ''), 'market': 'A',
            'sector': None,
            'pe': _safe_float(item.get('f9')),
            'pb': _safe_float(item.get('f23')),
            'ps': None,
            'market_cap': round(mc / 1e8, 2) if mc else None,
            'circulating_cap': None,
            'roe': _safe_float(item.get('f37')),
            'revenue': None,
            'revenue_growth': _safe_float(item.get('f38')),
            'profit': None,
            'profit_growth': _safe_float(item.get('f39')),
            'debt_ratio': _safe_float(item.get('f40')),
            'dividend_yield': _safe_float(item.get('f41')),
            'current_price': _safe_float(item.get('f2')),
            'high_52w': None, 'low_52w': None,
            'is_st': 1 if ('ST' in name or '*ST' in name) else 0,
            'list_date': None, 'snapshot_date': ds,
        })

    logger.info(f"[采集] 全A股完成: {len(records)}只")
    return records


# ── 初筛 ──

def pre_filter_stocks(records: list[dict], config: dict) -> list[dict]:
    cfg = config.get('screener', {}).get('conditions', {})
    candidates = []
    for s in records:
        if cfg.get('exclude_st', True) and s['is_st']:
            continue
        pe = s.get('pe')
        if pe is None or pe < cfg.get('min_pe', 3) or pe > cfg.get('max_pe', 20):
            continue
        pb = s.get('pb')
        if pb is not None and pb > cfg.get('max_pb', 3.5):
            continue
        mc = s.get('market_cap')
        if mc is not None and (mc < cfg.get('min_market_cap', 50) or mc > cfg.get('max_market_cap', 10000)):
            continue
        candidates.append(s)
    logger.info(f"[初筛] {len(records)} -> {len(candidates)}")
    return candidates


# ── 财务补充 ──

def enrich_financial_data(stocks: list[dict], workers=8) -> list[dict]:
    """并行补全财务指标（新浪源 stock_financial_abstract），覆盖 curl 原始值"""
    import akshare as ak

    need = stocks  # always fetch from Sina (curl EM values may be raw/unscaled)
    logger.info(f"[财务] 获取{len(need)}只财务指标 ({workers}线程)...")

    def _fetch(s):
        try:
            df = ak.stock_financial_abstract(symbol=s['code'])
            cols = [c for c in df.columns if c not in ('选项', '指标')]
            if not cols: return s
            lk = cols[0]  # newest period
            fin = {}
            for _, r in df.iterrows():
                fin[str(r.get('指标', ''))] = _safe_float(r.get(lk))
            # Always overwrite with Sina data
            s['roe'] = fin.get('净资产收益率(ROE)')
            s['debt_ratio'] = fin.get('资产负债率')
            s['revenue_growth'] = fin.get('营业总收入增长率')
            s['profit_growth'] = fin.get('归属母公司净利润增长率')
        except Exception:
            pass
        return s

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        fs = {ex.submit(_fetch, s): i for i, s in enumerate(stocks)}
        for f in concurrent.futures.as_completed(fs):
            done += 1
            if done % 20 == 0 or done == len(stocks):
                logger.info(f"[财务] {done}/{len(stocks)}")

    roe_ok = sum(1 for s in stocks if s.get('roe') is not None)
    logger.info(f"[财务] ROE数据: {roe_ok}/{len(stocks)}")
    return stocks


# ── 采集流水线 ──

def run_collect_pipeline(config: dict) -> list[dict]:
    """采集 → 快照 → 初筛 → 财务补充"""
    from src.models.database import MarketIndexDAO, StockSnapshotDAO

    indices = fetch_market_index()
    if indices:
        MarketIndexDAO().save(indices)
        for i in indices:
            logger.info(f"  {i['index_name']}: {i['current_value']}")

    records = fetch_all_stocks_basic()
    if not records:
        return []
    StockSnapshotDAO().save_batch(records)

    candidates = pre_filter_stocks(records, config)
    if not candidates:
        return []

    enrich_financial_data(candidates)
    return candidates
