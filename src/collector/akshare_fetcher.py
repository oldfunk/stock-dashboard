"""
A 股数据采集模块
数据源：腾讯全量行情API（含PE/PB/市值，通过curl绕过TUN阻断）
首次运行时用AKShare获取股票代码表并缓存，
后续运行直接读缓存+腾讯批查。

本模块是行情采集的唯一入口：
- 全A股行情 / 大盘指数 / 财务补充 都集中在此
- scheduler 与 scripts 均复用本模块函数，避免重复实现
"""

import json
import time
import logging
import os
import re
from collections import defaultdict

from src.utils import (
    safe_float, curl_get, tc_encode, parse_tc_line,
    parse_tc_indices, split_tc_response, INDEX_CODES, now_cn,
)

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(__file__), '../../data/cache/stock_codes.json')


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

def fetch_tencent_batch(codes: list[str], batch_size=80) -> list[dict]:
    """从腾讯API批量获取股票行情"""
    results, tc_codes = [], [tc_encode(c) for c in codes]
    for i in range(0, len(tc_codes), batch_size):
        batch = tc_codes[i:i + batch_size]
        raw = curl_get(f"https://qt.gtimg.cn/q={','.join(batch)}", timeout=30)
        if raw:
            for val in split_tc_response(raw):
                q = parse_tc_line(val)
                if q:
                    results.append(q)
        if i + batch_size < len(tc_codes):
            time.sleep(0.5)
    logger.info(f"[腾讯] {len(results)} 只")
    return results


# ── 大盘指数（统一实现，scheduler 复用）──

def fetch_market_index(max_retries: int = 2) -> list[dict]:
    """获取大盘指数（腾讯 qt.gtimg.cn，与 scheduler 统一数据源）。

    返回标准字典列表，字段与 MarketIndexDAO.save 兼容。
    """
    for att in range(max_retries):
        raw = curl_get(
            f"https://qt.gtimg.cn/q={','.join(INDEX_CODES)}", timeout=15
        )
        if not raw:
            if att < max_retries - 1:
                time.sleep(3)
            continue
        idx = parse_tc_indices(raw)
        if idx:
            logger.info(f"[采集] 大盘: {len(idx)} 条")
            return idx
        logger.warning(f"[采集] 大盘第{att+1}次解析为空")
        if att < max_retries - 1:
            time.sleep(3)
    return []


# ── 全A股行情 ──

def fetch_all_stocks_basic() -> list[dict]:
    """全A股行情：腾讯批查 + 股票代码缓存"""
    from src.models.database import StockSnapshotDAO
    ds = now_cn().strftime("%Y-%m-%d")

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
        if cfg.get('exclude_st', True) and s['is_st']:
            continue
        pe = s.get('pe')
        if pe is None or pe < cfg.get('min_pe', 3) or pe > cfg.get('max_pe', 20):
            continue
        pb = s.get('pb')
        if pb is not None and pb > cfg.get('max_pb', 3.5):
            continue
        mc = s.get('market_cap')
        if mc is not None and (mc < cfg.get('min_market_cap', 30) or mc > cfg.get('max_market_cap', 50000)):
            continue
        candidates.append(s)
    logger.info(f"[初筛] {len(records)} -> {len(candidates)}")
    return candidates


# ── 财务补充 ──

def _code_to_em(code: str) -> str:
    """6位代码转东财格式: 600519→600519.SH, 000807→000807.SZ"""
    return f"{code}.SH" if code.startswith(('6', '9')) else f"{code}.SZ"


def enrich_financial_data(stocks: list[dict], batch_size=200) -> list[dict]:
    """用东财 data center API 批量获取财务指标（ROE/负债率/营收增长/利润增长）。

    原地补充字段后返回同一列表。
    """
    import httpx
    total = len(stocks)
    logger.info(f"[财务] 获取 {total} 只（东财datacenter API）...")
    url = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'
    columns = 'SECUCODE,REPORT_DATE,ROEJQ,ZCFZL,TOTALOPERATEREVETZ,PARENTNETPROFITTZ,XSMLL,MGJYXJJE'

    done = 0
    for batch_start in range(0, total, batch_size):
        batch = stocks[batch_start:batch_start + batch_size]
        em_codes = ','.join(f'"{_code_to_em(s["code"])}"' for s in batch)
        params = {
            'reportName': 'RPT_F10_FINANCE_MAINFINADATA',
            'columns': columns,
            'filter': f'(SECUCODE in ({em_codes}))',
            'pageNumber': 1, 'pageSize': batch_size * 2,
            'sortTypes': '-1', 'sortColumns': 'REPORT_DATE',
            'source': 'HSF10', 'client': 'PC',
        }
        try:
            with httpx.Client(timeout=30) as client:
                r = client.get(url, params=params)
                data = r.json()
            if not data.get('result') or not data['result'].get('data'):
                logger.warning(f"[财务] 批次 {batch_start//batch_size+1} 无数据: {data.get('message','')}")
                done += len(batch)
                continue
            # 每只股票取最新年报（12-31）的指标；没有年报才退回季度数据
            code_map = {s['code']: s for s in batch}
            latest: dict = defaultdict(dict)
            for row in data['result']['data']:
                raw = row['SECUCODE'].split('.')[0]
                if raw not in code_map:
                    continue
                date = (row.get('REPORT_DATE') or '')[:10]
                is_annual = date.endswith('12-31')
                prev = latest[raw].get('date', '')
                if (not prev
                        or (is_annual and not prev.endswith('12-31'))
                        or (is_annual == prev.endswith('12-31') and date > prev)):
                    latest[raw] = {
                        'date': date,
                        'roe': safe_float(row.get('ROEJQ')),
                        'debt_ratio': safe_float(row.get('ZCFZL')),
                        'revenue_growth': safe_float(row.get('TOTALOPERATEREVETZ')),
                        'profit_growth': safe_float(row.get('PARENTNETPROFITTZ')),
                        'gross_margin': safe_float(row.get('XSMLL')),
                        'ocf_per_share': safe_float(row.get('MGJYXJJE')),
                        'is_annual': is_annual,
                    }
            for code, info in latest.items():
                s = code_map[code]
                s['roe'] = info['roe']
                s['debt_ratio'] = info['debt_ratio']
                s['revenue_growth'] = info['revenue_growth']
                s['profit_growth'] = info['profit_growth']
                s['gross_margin'] = info['gross_margin']
                s['ocf_per_share'] = info['ocf_per_share']
        except Exception as e:
            logger.warning(f"[财务] 批次 {batch_start//batch_size+1} 异常: {e}")
        done += len(batch)
        logger.info(f"[财务] {done}/{total}")

    with_roe = sum(1 for s in stocks if s.get('roe') is not None)
    with_gm = sum(1 for s in stocks if s.get('gross_margin') is not None)
    with_ocf = sum(1 for s in stocks if s.get('ocf_per_share') is not None)
    logger.info(f"[财务] ROE: {with_roe}/{total} | 毛利率: {with_gm}/{total} | OCF/股: {with_ocf}/{total}")
    return stocks


# ── 财务历史数据采集（本地数据仓库核心）──

def collect_historical_financial_data(all_stocks: list[dict]) -> dict:
    """从东财API拉取回历史财务数据（每只股票7年/25期），存入financial_history。

    all_stocks: 全A股行情列表（用于确定代码池）
    返回: {stock_code: True/False} 表示哪些股票获取到数据
    """
    from src.models.database import FinancialHistoryDAO

    # 只处理还没有历史数据的股票
    dao = FinancialHistoryDAO()
    todo = [s for s in all_stocks if not dao.has_code(s['code'])]
    if not todo:
        logger.info("[历史财务] 全部股票已有历史数据，跳过")
        return {}

    logger.info(f"[历史财务] 需要获取 {len(todo)} 只股票的历史数据...")

    collected = {}
    batch_size = 100  # 东财API最大安全pageSize
    url = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'
    columns = ('SECUCODE,REPORT_DATE,ROEJQ,ZCFZL,'
               'XSMLL,XSJLL,MGJYXJJE,'
               'TOTALOPERATEREVETZ,PARENTNETPROFITTZ,PARENTNETPROFIT')

    import httpx
    for batch_start in range(0, len(todo), batch_size):
        batch = todo[batch_start:batch_start + batch_size]
        em_codes = ','.join(f'"{_code_to_em(s["code"])}"' for s in batch)

        # 分2页拿满数据（首批5年+扩展）
        for page in [1, 2]:
            params = {
                'reportName': 'RPT_F10_FINANCE_MAINFINADATA',
                'columns': columns,
                'filter': f'(SECUCODE in ({em_codes}))',
                'pageNumber': page, 'pageSize': 100,
                'sortTypes': '-1', 'sortColumns': 'REPORT_DATE',
                'source': 'HSF10', 'client': 'PC',
            }
            try:
                with httpx.Client(timeout=30) as client:
                    r = client.get(url, params=params)
                    data = r.json()
                if not data.get('result') or not data['result'].get('data'):
                    continue

                # 处理每行数据
                records = []
                for row in data['result']['data']:
                    code = row['SECUCODE'].split('.')[0]
                    records.append({
                        'stock_code': code,
                        'report_date': (row.get('REPORT_DATE') or '')[:10],
                        'roe': safe_float(row.get('ROEJQ')),
                        'gross_margin': safe_float(row.get('XSMLL')),
                        'net_margin': safe_float(row.get('XSJLL')),
                        'ocf_per_share': safe_float(row.get('MGJYXJJE')),
                        'debt_ratio': safe_float(row.get('ZCFZL')),
                        'revenue_growth': safe_float(row.get('TOTALOPERATEREVETZ')),
                        'profit_growth': safe_float(row.get('PARENTNETPROFITTZ')),
                        'net_profit': safe_float(row.get('PARENTNETPROFIT')),
                    })
                    collected[code] = True

                dao.batch_save(records)
                logger.info(f"[历史财务] 批次 {batch_start//batch_size+1} 页{page}: {len(records)} 条")
            except Exception as e:
                logger.warning(f"[历史财务] 批次 {batch_start//batch_size+1} 页{page} 异常: {e}")

        time.sleep(0.5)

    logger.info(f"[历史财务] 完成: {len(collected)} 只股票")
    return collected


def rebuild_financial_summaries(stocks: list[dict]):
    """从 financial_history 重建 financial_summary 表（5年均值等）"""
    from src.models.database import FinancialHistoryDAO, FinancialSummaryDAO

    fh_dao = FinancialHistoryDAO()
    fs_dao = FinancialSummaryDAO()
    annual = fh_dao.get_all_annual(min_year=2020)

    # 按股票代码分组
    by_code = defaultdict(list)
    for r in annual:
        by_code[r['stock_code']].append(r)

    count = 0
    for code, reports in by_code.items():
        if len(reports) < 2:  # 至少2年年报才有意义
            continue
        reports.sort(key=lambda x: x['report_date'])

        # 取最近5年
        last5 = reports[-5:] if len(reports) >= 5 else reports

        # ROE 5年均值
        roe_vals = [r['roe'] for r in last5 if r['roe'] is not None]
        roe_avg = sum(roe_vals) / len(roe_vals) if roe_vals else None

        # 毛利率5年均值
        gm_vals = [r['gross_margin'] for r in last5 if r['gross_margin'] is not None]
        gm_avg = sum(gm_vals) / len(gm_vals) if gm_vals else None

        # 净利率5年均值
        nm_vals = [r['net_margin'] for r in last5 if r['net_margin'] is not None]
        nm_avg = sum(nm_vals) / len(nm_vals) if nm_vals else None

        # OCF/股趋势：比较最近3年的均值
        ocf_vals = [r['ocf_per_share'] for r in last5 if r['ocf_per_share'] is not None]
        ocf_latest = ocf_vals[-1] if ocf_vals else None
        ocf_positive = sum(1 for v in ocf_vals if v and v > 0) if ocf_vals else 0
        ocf_trend = 0
        if len(ocf_vals) >= 3:
            last3 = ocf_vals[-3:]
            if last3[-1] > last3[0] and last3[-1] > last3[1]:
                ocf_trend = 1  # 增长
            elif last3[-1] < last3[0] and last3[-1] < last3[1]:
                ocf_trend = -1  # 下降
            else:
                ocf_trend = 0  # 波动

        # 最新负债率
        debt_latest = last5[-1].get('debt_ratio') if last5 else None

        # 数据覆盖
        all_dates = [r['report_date'] for r in reports]
        data_years = f"{all_dates[0][:4]}-{all_dates[-1][:4]}" if all_dates else None

        fs_dao.save(code, {
            'roe_5y_avg': round(roe_avg, 2) if roe_avg else None,
            'roe_5y_count': len(roe_vals),
            'gross_margin_5y_avg': round(gm_avg, 2) if gm_avg else None,
            'net_margin_5y_avg': round(nm_avg, 2) if nm_avg else None,
            'ocf_5y_trend': ocf_trend,
            'ocf_latest': ocf_latest,
            'ocf_positive_years': ocf_positive,
            'debt_ratio_latest': debt_latest,
            'net_profit_5y_sum': None,  # placeholder
            'data_years': data_years,
        })
        count += 1

    logger.info(f"[财务汇总] 重建完成: {count} 只")


# ── 采集流水线 ──

def run_collect_pipeline(config: dict) -> list[dict]:
    """采集大盘指数 + 全A股行情 + 初筛 + 财务补充，返回候选股列表。"""
    from src.models.database import MarketIndexDAO

    indices = fetch_market_index()
    if indices:
        MarketIndexDAO().save(indices)
        for i in indices:
            logger.info(f"  {i['index_name']}: {i['current_value']}")

    records = fetch_all_stocks_basic()
    if not records:
        return []

    candidates = pre_filter_stocks(records, config)
    if not candidates:
        return []

    enrich_financial_data(candidates)
    return candidates
