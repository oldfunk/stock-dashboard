"""
AKShare 数据采集模块
获取 A 股全市场基本面数据、大盘指数
"""

import time
import logging
import random
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def fetch_market_index(max_retries=3) -> list[dict]:
    """
    获取大盘指数数据
    上证综指、深证成指、创业板指
    """
    import akshare as ak

    indices = []
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    ts = now.isoformat()

    for attempt in range(max_retries):
        try:
            df = ak.stock_zh_index_spot_em()
            target_indices = {
                '上证指数': '000001',
                '深证成指': '399001',
                '创业板指': '399006',
                '科创50': '000688',
            }
            for _, row in df.iterrows():
                name = str(row.get('名称', ''))
                code = str(row.get('代码', ''))
                if name in target_indices:
                    indices.append({
                        'index_code': code,
                        'index_name': name,
                        'current_value': float(row.get('最新价', 0)),
                        'change_percent': float(row.get('涨跌幅', 0)),
                        'change_amount': float(row.get('涨跌额', 0)),
                        'volume': float(row.get('成交量', 0)),
                        'amount': float(row.get('成交额', 0)),
                        'pe': None,
                        'pb': None,
                        'timestamp': ts,
                        'date': date_str,
                    })
            if indices:
                logger.info(f"[采集] 大盘指数 OK: {len(indices)} 条")
                return indices
        except Exception as e:
            logger.warning(f"[采集] 大盘指数第 {attempt+1} 次失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt + random.random())

    logger.error(f"[采集] 大盘指数彻底失败")
    return indices


def fetch_all_stocks_basic(max_retries=3) -> list[dict]:
    """
    获取全 A 股基础数据
    支持重试机制
    """
    import akshare as ak

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    records = []

    for attempt in range(max_retries):
        try:
            df = ak.stock_zh_a_spot_em()
            logger.info(f"[采集] 全A股行情 OK: {len(df)} 条")
            records = []
            for _, row in df.iterrows():
                code = str(row.get('代码', ''))
                name = str(row.get('名称', ''))
                is_st = 0
                if name.startswith('*ST') or name.startswith('ST'):
                    is_st = 1

                pe = _safe_float(row.get('市盈率-动态'))
                pb = _safe_float(row.get('市净率'))

                record = {
                    'code': code,
                    'name': name,
                    'market': 'A',
                    'sector': None,
                    'pe': pe,
                    'pb': pb,
                    'ps': None,
                    'market_cap': _safe_float(row.get('总市值')),
                    'circulating_cap': _safe_float(row.get('流通市值')),
                    'roe': None,
                    'revenue': None,
                    'revenue_growth': None,
                    'profit': None,
                    'profit_growth': None,
                    'debt_ratio': None,
                    'dividend_yield': None,
                    'current_price': _safe_float(row.get('最新价')),
                    'high_52w': None,
                    'low_52w': None,
                    'is_st': is_st,
                    'list_date': None,
                    'snapshot_date': date_str,
                }
                records.append(record)

            # 再补充财务指标（逐批获取，避免超时）
            logger.info(f"[采集] 补充财务指标...")
            _enrich_financial_data(records)
            logger.info(f"[采集] 全A股快照完成: {len(records)} 条")
            return records

        except Exception as e:
            logger.warning(f"[采集] 全A股第 {attempt+1} 次失败: {e}")
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1) + random.uniform(0, 3)
                logger.info(f"[采集] {int(wait)} 秒后重试...")
                time.sleep(wait)

    return records


def _enrich_financial_data(records: list[dict]):
    """
    批量补充财务指标
    分行业或分批获取，减少单次请求量
    """
    import akshare as ak

    try:
        df_fin = ak.stock_financial_analysis_indicator_em(symbol="全部")
        if df_fin is None or df_fin.empty:
            logger.warning("[采集] 财务指标数据为空")
            return

        # 取最新一期数据（按股票代码分组取最新）
        df_fin = df_fin.sort_values('REPORT_DATE', ascending=False)
        latest = df_fin.groupby('SECUCODE', sort=False).first().reset_index()

        logger.info(f"[采集] 财务指标 OK: {len(latest)} 只股票")
        enriched = 0

        # 建立快速查找字典
        fin_map = {}
        for _, row in latest.iterrows():
            secucode = str(row.get('SECUCODE', ''))
            code = secucode.replace('.SH', '').replace('.SZ', '')
            fin_map[code] = row

        for record in records:
            code = record['code']
            fin = fin_map.get(code)
            if fin is not None:
                record['roe'] = _safe_float(fin.get('ROE_JQ'))
                record['revenue'] = _safe_float(fin.get('OPERATE_INCOME'))
                record['revenue_growth'] = _safe_float(fin.get('YSTZ'))
                record['profit'] = _safe_float(fin.get('NET_PROFIT_ATSOPC'))
                record['profit_growth'] = _safe_float(fin.get('SJLTZ'))
                record['debt_ratio'] = _safe_float(fin.get('DEBT_ASSET_RATIO'))
                record['dividend_yield'] = _safe_float(fin.get('DIVIDEND_YIELD'))
                enriched += 1

        logger.info(f"[采集] 财务指标补充完成: {enriched}/{len(records)}")

    except Exception as e:
        logger.warning(f"[采集] 财务指标补充失败: {e}")


def _safe_float(val) -> Optional[float]:
    """安全转换 float，处理 NaN 和 None"""
    if val is None:
        return None
    try:
        v = float(val)
        import math
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 2)
    except (ValueError, TypeError):
        return None
