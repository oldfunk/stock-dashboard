"""
数据采集模块
使用系统 curl 绕过 sing-box TLS 指纹检测
直接调用东方财富 API 获取 A 股数据
"""

import time
import json
import logging
import subprocess
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _curl_get(url: str, timeout: int = 30) -> Optional[str]:
    """使用系统 curl 发起 GET 请求"""
    try:
        result = subprocess.run(
            [
                'curl', '-s', '--connect-timeout', str(timeout // 2),
                '--max-time', str(timeout),
                '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                '-H', 'Referer: https://quote.eastmoney.com/',
                url,
            ],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"[采集] curl 超时")
        return None
    except Exception as e:
        logger.error(f"[采集] curl 异常: {e}")
        return None


def _fetch_paginated(base_url: str, params: dict, page_size: int = 100,
                     max_pages: int = 60, max_retries: int = 5) -> list[dict]:
    """
    从东方财富分页 API 拉取全量数据
    东方财富每页最多返回 100 条
    使用系统 curl 避免 TLS 指纹封锁
    自动重试，跳过失败页面
    """
    import urllib.parse

    all_data = []
    total_items = 0
    consecutive_empty = 0

    for page in range(1, max_pages + 1):
        params['pn'] = str(page)
        params['pz'] = str(page_size)
        query = urllib.parse.urlencode(params, doseq=True)
        url = f'{base_url}?{query}'

        success = False
        for attempt in range(max_retries):
            text = _curl_get(url)
            if text:
                try:
                    data = json.loads(text)
                    if data.get('data') is None or data['data'].get('diff') is None:
                        consecutive_empty += 1
                        break  # retry loop

                    items = data['data']['diff']
                    if not items:
                        consecutive_empty += 1
                        break  # retry loop

                    all_data.extend(items)

                    total = data['data'].get('total', 0)
                    if total_items == 0 and total > 0:
                        total_items = total
                        expected_pages = (total + page_size - 1) // page_size
                        if expected_pages > 1:
                            logger.info(f"[采集] 共 {total} 条，约 {expected_pages} 页")

                    success = True
                    consecutive_empty = 0  # reset on success

                    # 已获取全部 → 结束
                    if total_items > 0 and len(all_data) >= total_items:
                        return all_data
                    break  # retry loop
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass

            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))

        if not success:
            logger.warning(f"[采集] 第 {page} 页失败")

        # 连续 3 次空页 → 结束
        if consecutive_empty >= 3:
            logger.info(f"[采集] 连续 {consecutive_empty} 页空，结束翻页")
            break

    logger.info(f"[采集] 共获取 {len(all_data)} 条")
    return all_data


def fetch_market_index() -> list[dict]:
    """获取大盘指数（上证综指、深证成指、创业板指、科创50）"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    ts = now.isoformat()

    all_items = []

    # 上证系列指数 (m:1+t:1)
    params = {
        'pn': '1', 'pz': '50', 'po': '1', 'np': '1',
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': 'm:1+t:1',
        'fields': 'f2,f3,f4,f5,f6,f12,f14',
    }
    all_items.extend(_fetch_paginated('https://48.push2.eastmoney.com/api/qt/clist/get', params))

    # 沪深重要指数 (b:MK0010) — 含上证指数、深证成指、创业板指等
    params2 = {
        'pn': '1', 'pz': '50', 'po': '1', 'np': '1',
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': '2', 'invt': '2', 'dect': '1', 'fid': '',
        'fs': 'b:MK0010',
        'fields': 'f2,f3,f4,f5,f6,f12,f14',
    }
    all_items.extend(_fetch_paginated('https://33.push2.eastmoney.com/api/qt/clist/get', params2))

    target_names = {
        '上证指数': '000001', '深证成指': '399001',
        '创业板指': '399006', '科创50': '000688',
    }

    indices = []
    seen = set()
    for item in all_items:
        name = str(item.get('f14', ''))
        if name in target_names and name not in seen:
            seen.add(name)
            indices.append({
                'index_code': str(item.get('f12', '')),
                'index_name': name,
                'current_value': float(item.get('f2', 0) or 0),
                'change_percent': float(item.get('f3', 0) or 0),
                'change_amount': float(item.get('f4', 0) or 0),
                'volume': float(item.get('f5', 0) or 0),
                'amount': float(item.get('f6', 0) or 0),
                'pe': None,
                'pb': None,
                'timestamp': ts,
                'date': date_str,
            })

    logger.info(f"[采集] 大盘指数: {len(indices)} 条 {[i['index_name'] for i in indices]}")
    return indices


def fetch_all_stocks_basic() -> list[dict]:
    """获取全 A 股行情+基础财务数据"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    params = {
        'pn': '1', 'pz': '500', 'po': '1', 'np': '1',
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048',
        'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f115,f152',
    }

    items = _fetch_paginated(
        'https://82.push2.eastmoney.com/api/qt/clist/get',
        params, page_size=500
    )

    if not items:
        return []

    records = []
    for item in items:
        name = str(item.get('f14', '') or '')
        code = str(item.get('f12', '') or '')
        if not code or not name:
            continue

        is_st = 1 if (name.startswith('*ST') or name.startswith('ST')) else 0

        records.append({
            'code': code,
            'name': name,
            'market': 'A',
            'sector': None,
            'pe': _f(item.get('f9')),     # 市盈率-动态
            'pb': _f(item.get('f23')),    # 市净率
            'ps': None,
            'market_cap': _f(item.get('f20')),  # 总市值
            'circulating_cap': _f(item.get('f21')),  # 流通市值
            'roe': None,
            'revenue': None,
            'revenue_growth': None,
            'profit': None,
            'profit_growth': None,
            'debt_ratio': None,
            'dividend_yield': None,
            'current_price': _f(item.get('f2')),  # 最新价
            'high_52w': _f(item.get('f15')),  # 最高
            'low_52w': _f(item.get('f16')),   # 最低
            'is_st': is_st,
            'list_date': None,
            'snapshot_date': date_str,
        })

    logger.info(f"[采集] 全A股: {len(records)} 只")

    # 补充财务指标（从财务分析API）
    _enrich_financial(records)

    return records


def _enrich_financial(records: list[dict]):
    """补充 ROE、营收增长、负债率等财务指标"""
    logger.info(f"[采集] 开始补充财务指标 ({len(records)} 只)...")

    # 分页获取全部股票的财务指标
    params = {
        'pn': '1', 'pz': '500', 'po': '1', 'np': '1',
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048',
        'fields': 'f9,f12,f23,f37,f38,f39,f40,f41,f42,f45,f46,f48,f49,f50,f57,f58,f84,f85,f86,f87,f88,f115,f125,f128,f140,f152,f162,f167,f168,f169,f170,f171,f172,f173,f257,f258,f266,f267,f268,f375,f376,f377',
    }

    items = _fetch_paginated(
        'https://82.push2.eastmoney.com/api/qt/clist/get',
        params, page_size=500
    )

    if not items:
        return

    # 建立快速查找
    fin_map = {}
    for item in items:
        code = str(item.get('f12', '') or '')
        if code:
            fin_map[code] = item

    enriched = 0
    for rec in records:
        fin = fin_map.get(rec['code'])
        if fin:
            # 东方财富字段映射:
            # f37=ROE, f39=营收增长%, f40=净利润增长%, f41=资产负债率%
            # f45=营业收入, f46=净利润, f57=股息率%
            rec['roe'] = _f(fin.get('f37'))
            rec['revenue'] = _f(fin.get('f45'))
            rec['revenue_growth'] = _f(fin.get('f39'))
            rec['profit'] = _f(fin.get('f46'))
            rec['profit_growth'] = _f(fin.get('f40'))
            rec['debt_ratio'] = _f(fin.get('f41'))
            rec['dividend_yield'] = _f(fin.get('f57'))
            enriched += 1

    logger.info(f"[采集] 财务指标补充: {enriched}/{len(records)}")


def _f(val) -> Optional[float]:
    """安全转 float"""
    if val is None or val == '-' or val == '':
        return None
    try:
        v = float(str(val).replace(',', ''))
        import math
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 2)
    except (ValueError, TypeError):
        return None
