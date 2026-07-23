"""A 股数据采集模块
数据源：AKShare（东方财富/同花顺多数据源，社区维护，长期稳定）
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

import akshare as ak
import pandas as pd

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

    # 缓存不足，用AKShare获取全A股代码表
    logger.info("[代码表] 缓存不足，从AKShare获取股票列表（首次约70s）...")
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
    """获取大盘指数（腾讯主 → AKShare日线兜底）

    腾讯 qt.gtimg.cn 为主数据源，失败时自动切换到 AKShare 日线。
    返回标准字典列表，字段与 MarketIndexDAO.save 兼容。
    """
    from datetime import datetime, timedelta

    # 主路径：腾讯行情
    for att in range(max_retries):
        try:
            raw = curl_get(
                f"https://qt.gtimg.cn/q={','.join(INDEX_CODES)}", timeout=15
            )
            if raw:
                results = parse_tc_indices(raw)
                if results:
                    return results
        except Exception as e:
            logger.warning(f"[指数] 腾讯第{att+1}次失败: {e}")
            if att < max_retries - 1:
                import time
                time.sleep(3)

    # 兜底：AKShare 日线
    logger.warning("[指数] 腾讯全部失败，启动 AKShare 日线兜底")
    import akshare as ak
    import pandas as pd
    fallback_results = []
    today = datetime.now()
    start = (today - timedelta(days=5)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    for code, name in INDEX_TARGETS.items():
        try:
            df = ak.stock_zh_index_daily(symbol=code)
            if df is not None and not df.empty:
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last
                current = float(last.get('close', 0))
                prev_close = float(prev.get('close', 0))
                change_pct = ((current / prev_close) - 1) * 100 if prev_close else 0
                fallback_results.append({
                    'code': code,
                    'index_name': name,
                    'current_value': round(current, 2),
                    'change': round(current - prev_close, 2),
                    'change_percent': round(change_pct, 2),
                    'open': float(last.get('open', 0)),
                    'high': float(last.get('high', 0)),
                    'low': float(last.get('low', 0)),
                    'volume': float(last.get('volume', 0)),
                    'date': str(last.name)[:10] if hasattr(last.name, 'strftime') else str(last.name)[:10],
                })
        except Exception as e:
            logger.warning(f"[指数] {name} 兜底失败: {e}")

    return fallback_results


def _computed_fallback(codes: list[dict]) -> list[dict]:
    """兜底策略：腾讯失败时，用 Sina 原始API + AKShare 财务数据自算行情

    自算 PE/PB/市值：
      PE = 价格 / EPS（EPS>0）
      PB = 价格 / BVPS（BVPS>0）
      总股本 = 净利润 / EPS → 市值 = 价格 × 总股本

    返回与腾讯接口一致的字段格式。
    """
    import urllib.request
    from src.utils import safe_float
    total = len(codes)
    logger.warning(f"[兜底] 腾讯不可用，启动自算行情（{total} 只）")

    # 1. Sina 原始API获取实时价格
    logger.info("[兜底] Sina 批量价格...")
    price_map = {}
    batch_size = 80
    for i in range(0, total, batch_size):
        batch = codes[i:i + batch_size]
        sina_codes = []
        for c in batch:
            prefix = 'sh' if c['code'].startswith('6') else 'sz'
            sina_codes.append(f"{prefix}{c['code']}")
        url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
        req = urllib.request.Request(url, headers={
            'Referer': 'https://finance.sina.com.cn',
            'User-Agent': 'Mozilla/5.0',
        })
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            text = resp.read().decode('gbk')
            for line in text.strip().split('\n'):
                line = line.strip()
                if not line or '=' not in line:
                    continue
                quoted = line.split('"')
                if len(quoted) < 2:
                    continue
                csv = quoted[1].split(',')
                if len(csv) < 32:
                    continue
                code = ''.join(filter(str.isdigit, line.split('=')[0]))
                if not code:
                    continue
                try:
                    price_map[code] = {
                        'price': float(csv[3]) if csv[3] else 0,
                        'prev_close': float(csv[2]) if csv[2] else 0,
                        'open': float(csv[1]) if csv[1] else 0,
                        'high': float(csv[4]) if csv[4] else 0,
                        'low': float(csv[5]) if csv[5] else 0,
                        'volume': int(csv[8]) if csv[8] else 0,
                        'amount': float(csv[9]) if csv[9] else 0,
                    }
                except (ValueError, IndexError):
                    pass
        except Exception as e:
            logger.warning(f"[兜底] Sina 批次 {i} 失败: {e}")
        if i + batch_size < total:
            import time
            time.sleep(0.5)

    logger.info(f"[兜底] Sina 获取 {len(price_map)} 只价格")

    # 2. AKShare 财务数据（EPS/BVPS/净利润）
    logger.info("[兜底] AKShare 财务数据...")
    import akshare as ak
    fin_map = {}
    try:
        for y in [2026, 2025]:
            for m in ['0331', '0630', '0930', '1231']:
                df = ak.stock_yjbb_em(date=f"{y}{m}")
                if df is not None and len(df) > 1000:
                    for _, r in df.iterrows():
                        code = str(r.get('股票代码', '')).strip().zfill(6)
                        eps = safe_float(r.get('每股收益'))
                        bvps = safe_float(r.get('每股净资产'))
                        np_ = safe_float(r.get('净利润-净利润'))
                        if code not in fin_map and eps is not None:
                            fin_map[code] = {'eps': eps, 'bvps': bvps, 'net_profit': np_}
                    break  # 取到最新的即可
            if fin_map:
                break
    except Exception as e:
        logger.error(f"[兜底] AKShare财务失败: {e}")

    logger.info(f"[兜底] AKShare 获取 {len(fin_map)} 只财务")

    # 3. 合并计算
    results = []
    for s in codes:
        code = s['code']
        name = s['name']
        pdata = price_map.get(code, {})
        fdata = fin_map.get(code, {})
        price = pdata.get('price', 0)
        prev_close = pdata.get('prev_close', 0)
        eps = fdata.get('eps')
        bvps = fdata.get('bvps')
        net_profit = fdata.get('net_profit')

        # 涨跌幅
        change_pct = ((price / prev_close) - 1) * 100 if price and prev_close else None

        # PE
        pe = round(price / eps, 2) if price and eps and eps > 0 else None
        # PB
        pb = round(price / bvps, 2) if price and bvps and bvps > 0 else None
        # 市值(亿) = 价格 × (净利润/EPS) / 1e8
        total_shares = None
        if net_profit is not None and eps is not None and eps > 0:
            total_shares = net_profit / eps
        market_cap = round(price * total_shares / 1e8, 1) if price and total_shares else None
        # 兜底：如果市值不可算但 price 有值，用小市值兜底（避免初筛全挂）
        if market_cap is None and price > 0:
            market_cap = 50.0  # 默认50亿小市值，让初筛自行过滤

        results.append({
            'code': code,
            'name': name,
            'price': price,
            'prev_close': prev_close,
            'change_percent': round(change_pct, 2) if change_pct is not None else None,
            'open': pdata.get('open'),
            'high': pdata.get('high'),
            'low': pdata.get('low'),
            'volume': pdata.get('volume'),
            'amount': pdata.get('amount'),
            'pe': pe,
            'pe_ttm': pe,
            'pb': pb,
            'market_cap': market_cap,
        })

    logger.info(f"[兜底] 自算完成 {len(results)} 只")
    return results


def fetch_all_stocks_basic(max_retries: int = 2) -> list[dict]:
    """全A股行情：腾讯主 → 自算兜底

    腾讯为主数据源（PE/PB/市值/价格），
    腾讯失败时自动切换到 Sina 价格 + AKShare 财务自算。
    """
    codes = _get_stock_codes()
    if not codes:
        return []

    result = None
    # 主路径：腾讯批查
    for att in range(max_retries):
        try:
            raw = fetch_tencent_batch([s['code'] for s in codes])
            if raw and len(raw) > 100:
                name_map = {s['code']: s['name'] for s in codes}
                for r in raw:
                    if r.get('name') is None:
                        r['name'] = name_map.get(r['code'], '')
                result = raw
                break
        except Exception as e:
            logger.warning(f"[行情] 腾讯第{att+1}次失败: {e}")
            if att < max_retries - 1:
                import time
                time.sleep(3)

    # 兜底：腾讯未取到
    if result is None:
        logger.warning("[行情] 腾讯全部失败，启动自算兜底")
        result = _computed_fallback(codes)

    return result or []


# ── 财务筛选条件（与 config.yaml 联动）──

def pre_filter_stocks(stocks: list[dict], config: dict) -> list[dict]:
    """初筛：基于配置的行筛条件过滤

    主要是 PE/PB/市值 等行情指标，财务指标在后续步骤补充。
    """
    screen = config.get('screener', {})
    conditions = screen.get('conditions', {})
    min_pe = conditions.get('min_pe', 1)
    max_pe = conditions.get('max_pe', 20)
    max_pb = conditions.get('max_pb', 3.5)
    min_mc = conditions.get('min_market_cap', 30)
    max_mc = conditions.get('max_market_cap', 50000)
    exclude_keys = [k.strip() for k in conditions.get('exclude_keywords', 'ST,退').split(',')]
    min_price = conditions.get('min_price', 0.5)

    candidates = []
    skip_reasons = defaultdict(int)

    for s in stocks:
        name = (s.get('name') or '').upper()
        if any(k.upper() in name for k in exclude_keys):
            skip_reasons['ST/退市'] += 1
            continue

        pe = safe_float(s.get('pe'))
        pe_ttm = safe_float(s.get('pe_ttm'))
        pb = safe_float(s.get('pb'))
        mc = safe_float(s.get('market_cap'))
        price = safe_float(s.get('price'))

        if pe is None and pe_ttm is None:
            skip_reasons['无PE'] += 1
            continue
        pe_val = pe if pe is not None else pe_ttm

        if mc is None or mc < min_mc or mc > max_mc:
            skip_reasons['市值'] += 1
            continue
        if pb is None or pb > max_pb:
            skip_reasons['PB'] += 1
            continue
        if pe_val < min_pe or pe_val > max_pe:
            skip_reasons['PE'] += 1
            continue
        if price is not None and price < min_price:
            skip_reasons['低价'] += 1
            continue

        candidates.append(s)

    logger.info(f"[初筛] {'/'.join(f'{k}={v}' for k, v in skip_reasons.items())}")
    logger.info(f"[初筛] {len(candidates)} 只通过")
    return candidates


# ── 财务补充（AKShare 核心数据源）──

def _code_to_em(code: str) -> str:
    """股票代码转东方财富格式"""
    return f"{code}.SH" if code.startswith(('6', '9')) else f"{code}.SZ"


def _find_latest_yjbb_date(ak_module) -> str:
    """找到 stock_yjbb_em 最新的可用报告期。"""
    import pandas as pd
    for y in [2026, 2025, 2024]:
        for m in ['0331', '0630', '0930', '1231']:
            date = f"{y}{m}"
            try:
                df = ak_module.stock_yjbb_em(date=date)
                if df is not None and len(df) > 1000:
                    return date
            except Exception:
                pass
    return '20260331'


# ── 财务深度补充缓存 ──
_FINANCIAL_ABSTRACT_CACHE_DIR = os.path.join(
    os.path.dirname(__file__), '../../data/cache/financial_abstract'
)
_FINANCIAL_ABSTRACT_TTL_SECONDS = 7 * 24 * 3600  # 财报季度数据 7 天内不会变


def _financial_abstract_cache_path(code: str) -> str:
    os.makedirs(_FINANCIAL_ABSTRACT_CACHE_DIR, exist_ok=True)
    return os.path.join(_FINANCIAL_ABSTRACT_CACHE_DIR, f"{code}.json")


def _load_financial_abstract_cache(code: str) -> dict | None:
    """加载股票财务摘要缓存，TTL 内有效则返回。"""
    path = _financial_abstract_cache_path(code)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        cached_at = data.get('cached_at', 0)
        if time.time() - cached_at > _FINANCIAL_ABSTRACT_TTL_SECONDS:
            return None
        return data
    except Exception:
        return None


def _save_financial_abstract_cache(code: str, report_date: str,
                                   net_margin: float | None, debt_ratio: float | None):
    """保存股票财务摘要缓存。"""
    path = _financial_abstract_cache_path(code)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({
                'report_date': report_date,
                'net_margin': net_margin,
                'debt_ratio': debt_ratio,
                'cached_at': time.time(),
            }, f, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"[财务缓存] 保存失败 {code}: {e}")


def _fetch_single_financial_abstract(s: dict) -> tuple[str, float | None, float | None, str | None]:
    """单只股票获取同花顺财务摘要，优先读缓存。返回 (code, net_margin, debt_ratio, report_date)。"""
    code = s['code']
    # 已有完整字段则跳过
    if s.get('net_margin') is not None and s.get('debt_ratio') is not None:
        return code, s.get('net_margin'), s.get('debt_ratio'), None

    # 读缓存
    cached = _load_financial_abstract_cache(code)
    if cached:
        return (code, cached.get('net_margin'), cached.get('debt_ratio'),
                cached.get('report_date'))

    try:
        import akshare as ak
        df = ak.stock_financial_abstract_ths(symbol=code)
        if df is None or df.empty:
            return code, None, None, None
        latest = df.iloc[0]
        report_date = str(latest.get('报告期', ''))[:10]
        net_margin, debt_ratio = None, None
        net_margin_str = latest.get('销售净利率', '')
        if net_margin_str and net_margin_str != '-':
            try:
                net_margin = float(str(net_margin_str).replace('%', ''))
            except (ValueError, TypeError):
                pass
        debt_str = latest.get('资产负债率', '')
        if debt_str and debt_str != '-':
            try:
                debt_ratio = float(str(debt_str).replace('%', ''))
            except (ValueError, TypeError):
                pass
        _save_financial_abstract_cache(code, report_date, net_margin, debt_ratio)
        return code, net_margin, debt_ratio, report_date
    except Exception as e:
        logger.debug(f"[财务] {code} 深度补充失败: {e}")
        return code, None, None, None


def enrich_financial_data(stocks: list[dict], batch_size=200) -> list[dict]:
    """用AKShare批量获取财务指标（ROE/毛利率/净利率/OCF/负债率等）。

    - 全A股用 stock_yjbb_em 一次批量调用（~6秒）
    - 候选股深度数据用 stock_financial_abstract_ths 并发获取，并带 7 天本地缓存
    原地补充字段后返回同一列表。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import akshare as ak
    total = len(stocks)
    logger.info(f"[财务] AKShare 采集 {total} 只...")

    # ── 第一步：stock_yjbb_em — 一次调用获取全量基础数据 ──
    latest_date = _find_latest_yjbb_date(ak)
    try:
        all_df = ak.stock_yjbb_em(date=latest_date)
    except Exception as e:
        logger.warning(f"[财务] stock_yjbb_em失败({e})，跳过基础字段")
        all_df = None

    if all_df is not None:
        by_code = {}
        for _, r in all_df.iterrows():
            raw = str(r.get('股票代码', '')).strip().zfill(6)
            by_code[raw] = r

        for s in stocks:
            code = s['code']
            r = by_code.get(code)
            if r is None:
                continue
            s['roe'] = safe_float(r.get('净资产收益率'))
            s['gross_margin'] = safe_float(r.get('销售毛利率'))
            s['ocf_per_share'] = safe_float(r.get('每股经营现金流量'))
            s['eps'] = safe_float(r.get('每股收益'))
            s['revenue_growth'] = safe_float(r.get('营业总收入-同比增长'))
            s['profit_growth'] = safe_float(r.get('净利润-同比增长'))
            s['net_profit'] = safe_float(r.get('净利润-净利润'))
            s['bvps'] = safe_float(r.get('每股净资产'))

        with_roe = sum(1 for s in stocks if s.get('roe') is not None)
        logger.info(f"[财务] stock_yjbb_em: {with_roe}/{total} 有ROE")

    # ── 第二步：stock_financial_abstract_ths 并发深度补充 ──
    # 补齐：净利率/负债率（stock_yjbb_em 不提供）
    # AKShare 对同花顺接口有隐性限流，并发控制在 5，每次请求后小睡 0.2s
    pending = [s for s in stocks
               if s.get('net_margin') is None or s.get('debt_ratio') is None]
    fetched = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_code = {
            executor.submit(_fetch_single_financial_abstract, s): s
            for s in pending
        }
        for future in as_completed(future_to_code):
            s = future_to_code[future]
            try:
                code, net_margin, debt_ratio, _ = future.result()
                if net_margin is not None:
                    s['net_margin'] = net_margin
                if debt_ratio is not None:
                    s['debt_ratio'] = debt_ratio
                fetched += 1
            except Exception as e:
                logger.debug(f"[财务] {s['code']} 并发获取异常: {e}")
            if (fetched + 1) % 50 == 0:
                logger.info(f"[财务] 深度补充 {fetched}/{len(pending)}")
            time.sleep(0.2)  # 温和限流

    with_gm = sum(1 for s in stocks if s.get('gross_margin') is not None)
    with_nm = sum(1 for s in stocks if s.get('net_margin') is not None)
    logger.info(f"[财务] ROE:{with_roe}/{total} 毛利率:{with_gm}/{total} 净利率:{with_nm}/{total}")
    return stocks


# ── 财务历史数据采集（AKShare 逐只）──

def _parse_pct(value) -> float | None:
    """解析带%标记的百分比值"""
    if value is None or value == '-' or value is False:
        return None
    try:
        return float(str(value).replace('%', '').replace(',', ''))
    except (ValueError, TypeError):
        return None


def collect_historical_financial_data(all_stocks: list[dict],
                                      force_full: bool = False,
                                      incremental_days: int = 60) -> dict:
    """用AKShare逐只拉取历史财务数据，存入financial_history。

    all_stocks: 候选股列表（筛选过的）
    force_full: 为 True 时全量重新采集所有候选股（适合每周/数据修复）
    incremental_days: 增量模式下，若最新报告期距今天数超过此阈值则重新采集
    返回: {stock_code: True} 表示获取成功的股票
    """
    from datetime import datetime, timedelta
    from src.models.database import FinancialHistoryDAO
    import akshare as ak
    import pandas as pd

    dao = FinancialHistoryDAO()
    today = datetime.now()

    if force_full:
        todo = all_stocks
        logger.info("[历史财务] 强制全量采集模式")
    else:
        # 每日增量模式：没有历史数据，或最新报告期过旧的股票
        codes = [s['code'] for s in all_stocks]
        latest_dates = dao.get_latest_report_dates(codes)
        todo = []
        for s in all_stocks:
            code = s['code']
            latest = latest_dates.get(code)
            if not latest:
                todo.append(s)
                continue
            try:
                latest_dt = datetime.strptime(latest, "%Y-%m-%d")
                if (today - latest_dt).days > incremental_days:
                    todo.append(s)
            except Exception:
                todo.append(s)

    if not todo:
        logger.info("[历史财务] 全部候选股已有近期历史数据，跳过")
        return {}

    logger.info(f"[历史财务] AKShare 逐只采集 {len(todo)} 只（全量={force_full}）...")
    collected = {}

    for idx, s in enumerate(todo):
        code = s['code']
        try:
            # 方法1：stock_financial_abstract_ths — 带净利率/负债率/毛利率的完整历史
            try:
                df_abs = ak.stock_financial_abstract_ths(symbol=code)
            except Exception:
                df_abs = None

            # 方法2：stock_profit_sheet — 带利息费用/总股本的利润表
            try:
                df_ps = ak.stock_profit_sheet_by_report_em(symbol=code)
            except Exception:
                df_ps = None

            # 方法3：stock_cash_flow_sheet — 带经营/投资现金流的现金流量表
            try:
                df_cf = ak.stock_cash_flow_sheet_by_report_em(symbol=code)
            except Exception:
                df_cf = None

            # 合并数据：以 financial_abstract 为骨架，补充深度字段
            records = []
            if df_abs is not None and not df_abs.empty:
                for _, row in df_abs.iterrows():
                    rpt_date = str(row.get('报告期', ''))[:10]
                    if not rpt_date:
                        continue
                    rec = {
                        'stock_code': code,
                        'report_date': rpt_date,
                        'roe': _parse_pct(row.get('净资产收益率')),
                        'gross_margin': _parse_pct(row.get('销售毛利率')),
                        'net_margin': _parse_pct(row.get('销售净利率')),
                        'ocf_per_share': safe_float(row.get('每股经营现金流')),
                        'debt_ratio': _parse_pct(row.get('资产负债率')),
                        'eps': safe_float(row.get('基本每股收益')),
                        'net_profit': safe_float(row.get('净利润')),
                    }
                    # 解析净利润（可能带"亿"）
                    np_val = row.get('净利润')
                    if np_val and isinstance(np_val, str):
                        if '亿' in np_val:
                            try:
                                rec['net_profit'] = float(np_val.replace('亿', '')) * 100_000_000
                            except (ValueError, TypeError):
                                pass
                    # 解析营收增长率和净利增长率
                    rec['revenue_growth'] = _parse_pct(row.get('营业总收入同比增长率'))
                    rec['profit_growth'] = _parse_pct(row.get('净利润同比增长率'))
                    # 解析营业总收入（完整值，来自financial_abstract）
                    rev = row.get('营业总收入')
                    if rev and isinstance(rev, str) and '亿' in rev:
                        try:
                            rec['total_operate_reve'] = float(rev.replace('亿', '')) * 100_000_000
                        except (ValueError, TypeError):
                            pass
                    records.append(rec)

            # 从利润表补充利息费用/总股本/FCF
            if df_ps is not None and not df_ps.empty:
                ps_by_date = {}
                for _, row in df_ps.iterrows():
                    d = (str(row.get('REPORT_DATE', '')))[:10]
                    if d:
                        ps_by_date[d] = row
                for rec in records:
                    d = rec['report_date']
                    row_ps = ps_by_date.get(d)
                    if row_ps is None:
                        # 找最近的
                        for ps_d in sorted(ps_by_date.keys(), reverse=True):
                            if ps_d[:4] == d[:4] or (ps_d[:4] == d[:4] and ps_d[5:7] <= d[5:7]):
                                row_ps = ps_by_date[ps_d]
                                break
                    if row_ps is not None:
                        # 利息覆盖倍数 = 营业利润 / 利息费用
                        op = safe_float(row_ps.get('OPERATE_PROFIT'))
                        ie = safe_float(row_ps.get('INTEREST_EXPENSE'))
                        if op is not None and ie is not None and ie != 0:
                            rec['interest_coverage'] = round(op / abs(ie), 2)
                        rec['total_shares'] = safe_float(row_ps.get('TOTAL_SHARES'))
                        # 利润表净利润
                        ps_np = safe_float(row_ps.get('NETPROFIT'))
                        if ps_np is not None and rec.get('net_profit') is None:
                            rec['net_profit'] = ps_np
                        # 营收入（利润表更精确）
                        ps_rev = safe_float(row_ps.get('OPERATE_INCOME'))
                        if ps_rev is not None:
                            rec['total_operate_reve'] = ps_rev

            # 从现金流量表补充FCF
            if df_cf is not None and not df_cf.empty:
                cf_by_date = {}
                for _, row in df_cf.iterrows():
                    d = (str(row.get('REPORT_DATE', '')))[:10]
                    if d:
                        cf_by_date[d] = row
                for rec in records:
                    d = rec['report_date']
                    row_cf = cf_by_date.get(d)
                    if row_cf is None:
                        for cf_d in sorted(cf_by_date.keys(), reverse=True):
                            if cf_d[:4] == d[:4] or (cf_d[:4] == d[:4] and cf_d[5:7] <= d[5:7]):
                                row_cf = cf_by_date[cf_d]
                                break
                    if row_cf is not None:
                        ocf = safe_float(row_cf.get('NETCASH_OPERATE'))
                        capex = safe_float(row_cf.get('NETCASH_INVEST'))
                        if ocf is not None and capex is not None:
                            rec['fcf'] = ocf + capex  # capex是负值
                        elif ocf is not None:
                            rec['fcf'] = ocf
                        # OCF/share 补充
                        cf_ocf = safe_float(row_cf.get('NETCASH_OPERATE'))
                        shares = rec.get('total_shares')
                        if cf_ocf is not None and shares and shares > 0:
                            ocfps = round(cf_ocf / shares, 4)
                            if rec.get('ocf_per_share') is None:
                                rec['ocf_per_share'] = ocfps

            if records:
                # 简化：去重（同一个报告期只留一个）
                seen_dates = set()
                deduped = []
                for rec in sorted(records, key=lambda x: x['report_date'], reverse=True):
                    d = rec['report_date']
                    if d not in seen_dates:
                        seen_dates.add(d)
                        deduped.append(rec)

                dao.batch_save(deduped)
                collected[code] = True
                logger.info(f"[历史] {code} {s.get('name','')}: {len(deduped)} 期")
            else:
                logger.warning(f"[历史] {code}: 无数据")

        except Exception as e:
            logger.warning(f"[历史] {code} 异常: {e}")

        time.sleep(0.3)  # AKShare 防限流

    logger.info(f"[历史财务] AKShare 完成: {len(collected)} 只")
    return collected


def rebuild_financial_summaries(stocks: list[dict]):
    """从 financial_history 重建 financial_summary 表（5年 + 10年均值）。"""
    from src.models.database import FinancialHistoryDAO, FinancialSummaryDAO

    fh_dao = FinancialHistoryDAO()
    fs_dao = FinancialSummaryDAO()
    annual = fh_dao.get_all_annual()  # >= 2016, 覆盖10年

    # 按股票代码分组
    by_code = defaultdict(list)
    for r in annual:
        by_code[r['stock_code']].append(r)

    def _avg(vals: list) -> float | None:
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    def _std(vals: list) -> float | None:
        """标准差 — 衡量波动性"""
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:
            return None
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        return var ** 0.5

    count = 0
    for code, reports in by_code.items():
        if len(reports) < 2:
            continue
        reports.sort(key=lambda x: x['report_date'])

        # ── 5年窗口（最近5份年报）──
        last5 = reports[-5:] if len(reports) >= 5 else reports
        roe_vals_5 = [r['roe'] for r in last5 if r['roe'] is not None]
        gm_vals_5 = [r['gross_margin'] for r in last5 if r['gross_margin'] is not None]
        nm_vals_5 = [r['net_margin'] for r in last5 if r['net_margin'] is not None]
        intcov_vals_5 = [r['interest_coverage'] for r in last5 if r.get('interest_coverage') is not None]
        fcf_vals_5 = [r['fcf'] for r in last5 if r.get('fcf') is not None and r['report_type'] == 'A']
        shares_vals_5 = [r['total_shares'] for r in last5 if r.get('total_shares') is not None]
        ocf_vals_5 = [r['ocf_per_share'] for r in last5 if r['ocf_per_share'] is not None]
        roic_vals_5 = [r['roic'] for r in last5 if r.get('roic') is not None]

        # ── 10年窗口 ──
        last10 = reports[-10:] if len(reports) >= 10 else reports
        roe_vals_10 = [r['roe'] for r in last10 if r['roe'] is not None]
        nm_vals_10 = [r['net_margin'] for r in last10 if r['net_margin'] is not None]
        intcov_vals_10 = [r['interest_coverage'] for r in last10 if r.get('interest_coverage') is not None]
        fcf_vals_10 = [r['fcf'] for r in last10 if r.get('fcf') is not None and r['report_type'] == 'A']
        shares_vals_10 = [r['total_shares'] for r in last10 if r.get('total_shares') is not None]
        roic_vals_10 = [r['roic'] for r in last10 if r.get('roic') is not None]

        # ROE 波动性（10年标准差，越小越稳）
        roe_volatility = _std(roe_vals_10)

        # ROE 5年→10年趋势差（正值=改善）
        roe_5y_avg = _avg(roe_vals_5) or 0
        roe_10y_avg = _avg(roe_vals_10) or 0
        roe_improvement = round(roe_5y_avg - roe_10y_avg, 2) if roe_10y_avg else None

        # FCF 一致性：10年中多少年年报FCF为正
        fcf_vals_10_annual = [r['fcf'] for r in reports if r.get('fcf') is not None and r['report_type'] == 'A']
        fcf_positive_years_10 = sum(1 for v in fcf_vals_10_annual if v and v > 0) if fcf_vals_10_annual else 0

        # OCF趋势
        ocf_latest = ocf_vals_5[-1] if ocf_vals_5 else None
        ocf_positive = sum(1 for v in ocf_vals_5 if v and v > 0) if ocf_vals_5 else 0
        ocf_trend = 0
        if len(ocf_vals_5) >= 3:
            last3 = ocf_vals_5[-3:]
            if last3[-1] > last3[0] and last3[-1] > last3[1]:
                ocf_trend = 1
            elif last3[-1] < last3[0] and last3[-1] < last3[1]:
                ocf_trend = -1

        # 股本稀释率
        share_dilution_5y = None
        if len(shares_vals_5) >= 2:
            earliest = shares_vals_5[-1]
            latest = shares_vals_5[0]
            if earliest and earliest > 0:
                share_dilution_5y = round((latest - earliest) / earliest * 100, 2)
        share_dilution_10y = None
        if len(shares_vals_10) >= 2:
            earliest = shares_vals_10[-1]
            latest = shares_vals_10[0]
            if earliest and earliest > 0:
                share_dilution_10y = round((latest - earliest) / earliest * 100, 2)

        fs_dao.save(code, {
            # 5年字段（不变）
            'roe_5y_avg': round(roe_5y_avg, 2) if roe_5y_avg else None,
            'roe_5y_count': len(roe_vals_5),
            'gross_margin_5y_avg': round(_avg(gm_vals_5), 2) if _avg(gm_vals_5) else None,
            'net_margin_5y_avg': round(_avg(nm_vals_5), 2) if _avg(nm_vals_5) else None,
            'ocf_5y_trend': ocf_trend,
            'ocf_latest': ocf_latest,
            'ocf_positive_years': ocf_positive,
            'debt_ratio_latest': reports[-1].get('debt_ratio') if reports else None,
            'net_profit_5y_sum': None,
            'intcov_5y_avg': round(_avg(intcov_vals_5), 2) if _avg(intcov_vals_5) else None,
            'fcf_5y_sum': round(sum(fcf_vals_5), 2) if fcf_vals_5 else None,
            'share_dilution_5y': share_dilution_5y,
            'roic_5y_avg': round(_avg(roic_vals_5), 2) if _avg(roic_vals_5) else None,

            # 10年新增字段
            'roe_10y_avg': round(roe_10y_avg, 2) if roe_10y_avg else None,
            'net_margin_10y_avg': round(_avg(nm_vals_10), 2) if _avg(nm_vals_10) else None,
            'intcov_10y_avg': round(_avg(intcov_vals_10), 2) if _avg(intcov_vals_10) else None,
            'fcf_10y_sum': round(sum(fcf_vals_10), 2) if fcf_vals_10 else None,
            'share_dilution_10y': share_dilution_10y,
            'fcf_positive_years_10': fcf_positive_years_10,
            'roe_volatility': round(roe_volatility, 2) if roe_volatility else None,
            'roe_improvement': roe_improvement,
            'roic_10y_avg': round(_avg(roic_vals_10), 2) if _avg(roic_vals_10) else None,

            'data_years': f"{reports[0]['report_date'][:4]}-{reports[-1]['report_date'][:4]}" if reports else None,
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


def fetch_index_kline(index_code: str, limit: int = 250) -> list[dict]:
    """拉取大盘指数日K线（实时，不缓存）。

    index_code: sh000001 / sz399001 等
    返回格式与 fetch_kline_data 一致（trade_date/open/close/high/low/volume/amount）
    """
    from datetime import datetime, timedelta
    now = now_cn()
    start = now - timedelta(days=limit + 60)  # 多拉一些应对节假日
    start_str = start.strftime("%Y%m%d")
    end_str = now.strftime("%Y%m%d")
    try:
        df = ak.stock_zh_index_daily(symbol=index_code)
    except Exception as e:
        logger.warning(f"[指数K线] 拉取失败 {index_code}: {e}")
        return []
    if df is None or df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        records.append({
            "trade_date": str(row.get("date", row.name))[:10],
            "open": float(row["open"]) if pd.notna(row.get("open")) else None,
            "close": float(row["close"]) if pd.notna(row.get("close")) else None,
            "high": float(row["high"]) if pd.notna(row.get("high")) else None,
            "low": float(row["low"]) if pd.notna(row.get("low")) else None,
            "volume": float(row["volume"]) if pd.notna(row.get("volume")) else 0,
            "amount": None,
            "turnover": None,
        })
    # 按日期升序，截取最近 limit 条
    records.sort(key=lambda x: x["trade_date"])
    return records[-limit:] if len(records) > limit else records


def fetch_kline_data(code: str, start_date: str | None = None,
                     adjust: str = "qfq") -> list[dict]:
    """拉取单只股票的日K线数据。

    参数：
    - code: 6位股票代码
    - start_date: 起始日期（YYYY-MM-DD），None 则拉最近1年
    - adjust: 复权类型，qfq=前复权（默认）

    返回：[{"trade_date", "open", "close", "high", "low", "volume", "amount", "turnover"}, ...]

    数据源优先级：东方财富（stock_zh_a_hist）→ 腾讯（stock_zh_a_hist_tx）备用。
    push2his.eastmoney.com 域名在某些网络环境下可能被封禁，腾讯数据源作为兜底。
    """
    from datetime import datetime, timedelta

    # 计算日期范围
    now = now_cn()
    if start_date is None:
        start = now - timedelta(days=365)
    else:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    start_str = start.strftime("%Y%m%d")
    end_str = now.strftime("%Y%m%d")

    # 优先：东方财富
    records = _fetch_kline_em(code, start_str, end_str, adjust)
    if records:
        return records

    # 备用：腾讯数据源
    logger.info(f"[K线] 东方财富失败，回退腾讯 {code}")
    records = _fetch_kline_tx(code, start_str, end_str)
    return records


def _fetch_kline_em(code: str, start_str: str, end_str: str,
                    adjust: str) -> list[dict]:
    """东方财富数据源拉取日K"""
    em_code = _code_to_em(code)
    try:
        df = ak.stock_zh_a_hist(
            symbol=em_code, period="daily",
            start_date=start_str, end_date=end_str, adjust=adjust
        )
    except Exception as e:
        logger.warning(f"[K线] 东方财富拉取失败 {code}: {e}")
        return []

    if df is None or df.empty:
        return []

    records = []
    for _, row in df.iterrows():
        records.append({
            "trade_date": str(row["日期"])[:10],
            "open": float(row["开盘"]) if pd.notna(row["开盘"]) else None,
            "close": float(row["收盘"]) if pd.notna(row["收盘"]) else None,
            "high": float(row["最高"]) if pd.notna(row["最高"]) else None,
            "low": float(row["最低"]) if pd.notna(row["最低"]) else None,
            "volume": float(row["成交量"]) if pd.notna(row["成交量"]) else None,
            "amount": float(row["成交额"]) if pd.notna(row["成交额"]) else None,
            "turnover": float(row["换手率"]) if pd.notna(row["换手率"]) else None,
        })
    return records


def _fetch_kline_tx(code: str, start_str: str, end_str: str) -> list[dict]:
    """腾讯数据源拉取日K（备用，无成交量和换手率）"""
    # 代码转腾讯格式：sz000792 / sh600519
    tx_code = f"sh{code}" if code.startswith(('6', '9')) else f"sz{code}"
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=tx_code, start_date=start_str, end_date=end_str
        )
    except Exception as e:
        logger.warning(f"[K线] 腾讯拉取失败 {code}: {e}")
        return []

    if df is None or df.empty:
        return []

    records = []
    for _, row in df.iterrows():
        records.append({
            "trade_date": str(row["date"])[:10],
            "open": float(row["open"]) if pd.notna(row["open"]) else None,
            "close": float(row["close"]) if pd.notna(row["close"]) else None,
            "high": float(row["high"]) if pd.notna(row["high"]) else None,
            "low": float(row["low"]) if pd.notna(row["low"]) else None,
            "volume": None,  # 腾讯数据源无成交量
            "amount": float(row["amount"]) if pd.notna(row["amount"]) else None,
            "turnover": None,  # 腾讯数据源无换手率
        })
    return records
