"""
股票看板 - 公共工具函数

集中放置跨模块复用的纯工具函数：
- 时区感知的当前时间
- 安全类型转换
- HTTP 抓取（curl 子进程，绕过 TUN 代理阻断）
- 腾讯行情解析（个股 + 大盘指数）
"""

import logging
import math
import subprocess
from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    _CN_TZ = ZoneInfo("Asia/Shanghai")
except ImportError:  # Python < 3.9 兜底
    _CN_TZ = None

logger = logging.getLogger(__name__)


def now_cn() -> datetime:
    """返回 Asia/Shanghai 时区的当前时间（剥离 tzinfo，保持与历史代码一致）"""
    if _CN_TZ is not None:
        return datetime.now(_CN_TZ).replace(tzinfo=None)
    return datetime.now()


def safe_float(val) -> Optional[float]:
    """安全浮点转换：过滤 None/NaN/Inf，保留两位小数"""
    if val is None:
        return None
    try:
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 2)
    except (ValueError, TypeError):
        return None


def curl_get(url: str, timeout: int = 20) -> Optional[str]:
    """通过 curl 子进程发起 GET 请求（绕过 TUN 代理对 Python httpx 的阻断）"""
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', '6',
             '--max-time', str(timeout), url],
            capture_output=True, timeout=timeout + 5)
        if r.returncode == 0 and r.stdout:
            try:
                return r.stdout.decode('utf-8')
            except UnicodeDecodeError:
                return r.stdout.decode('gbk', errors='replace')
    except Exception as e:
        logger.debug(f"[curl] {url} 失败: {e}")
    return None


def tc_encode(code: str) -> str:
    """6位股票代码转腾讯行情格式：600519→sh600519，000807→sz000807，900951→sh900951"""
    c = code.strip()
    if c.startswith('6') or c.startswith('9'):
        return f"sh{c}"
    return f"sz{c}"


def parse_tc_line(raw: str) -> Optional[dict]:
    """解析腾讯个股行情行（v_sh600519="..." 内的双引号内容）"""
    try:
        parts = raw.split('~')
        if len(parts) < 50:
            return None
        return {
            'code': parts[2].strip(),
            'name': parts[1].strip(),
            'current_price': safe_float(parts[3]),
            'prev_close': safe_float(parts[4]),
            'open_price': safe_float(parts[5]),
            'volume': safe_float(parts[6]),
            'change_amount': safe_float(parts[31]),
            'change_percent': safe_float(parts[32]),
            'high': safe_float(parts[33]),
            'low': safe_float(parts[34]),
            'amount': safe_float(parts[37]),
            'turnover_rate': safe_float(parts[38]) if len(parts) > 38 else None,
            'pe': safe_float(parts[39]) if len(parts) > 39 else None,
            'market_cap': safe_float(parts[45]) if len(parts) > 45 else None,
            'circulating_cap': safe_float(parts[44]) if len(parts) > 44 else None,
            'amplitude': safe_float(parts[43]) if len(parts) > 43 else None,
            'pb': safe_float(parts[46]) if len(parts) > 46 else None,
        }
    except Exception:
        return None


# ── 大盘指数 ──

INDEX_TARGETS: dict[str, str] = {
    'sh000001': '上证指数',
    'sz399001': '深证成指',
    'sz399006': '创业板指',
    'sh000688': '科创50',
    'sh000300': '沪深300',
    'sh000016': '上证50',
    'sh000905': '中证500',
    'sz399852': '中证1000',
    'sh000015': '红利指数',
    'sz399932': '中证消费',
}
INDEX_CODES: list[str] = list(INDEX_TARGETS.keys())


def parse_tc_indices(raw: str) -> list[dict]:
    """解析腾讯大盘指数行情返回的标准字典列表"""
    now = now_cn()
    ds, ts = now.strftime("%Y-%m-%d"), now.isoformat()
    indices: list[dict] = []
    for line in raw.strip().split('\n'):
        if '=' not in line:
            continue
        line = line.split(';')[0]
        eq = line.find('=')
        if eq < 0:
            continue
        val = line[eq + 1:].strip().strip('"')
        parts = val.split('~')
        if len(parts) < 35:
            continue
        code = parts[2].strip()
        # 上证/科创50 以 000/688 开头（属 "00" 前缀）→ sh；深证/创业以 399 开头 → sz
        tc_key = f"{'sh' if code.startswith('00') else 'sz'}{code}"
        name = INDEX_TARGETS.get(tc_key)
        if not name:
            continue
        cur = safe_float(parts[3])
        yes_close = safe_float(parts[4])
        chg_pct = ((cur - yes_close) / yes_close * 100) if (cur and yes_close and yes_close != 0) else None
        chg_amt = (cur - yes_close) if (cur and yes_close) else None
        volume = safe_float(parts[6]) if len(parts) > 6 else None
        amount = safe_float(parts[37]) if len(parts) > 37 else None
        indices.append({
            'index_code': tc_key,
            'index_name': name,
            'current_value': cur or 0,
            'change_percent': safe_float(chg_pct),
            'change_amount': safe_float(chg_amt),
            'volume': volume or 0,
            'amount': amount or 0,
            'pe': None, 'pb': None,
            'timestamp': ts, 'date': ds,
        })
    return indices


def split_tc_response(raw: str) -> list[str]:
    """拆分腾讯批量响应为各行的 val（双引号内容）列表"""
    out: list[str] = []
    for line in raw.strip().split('\n'):
        if '=' not in line:
            continue
        line = line.split(';')[0]
        eq = line.find('=')
        if eq < 0:
            continue
        out.append(line[eq + 1:].strip().strip('"'))
    return out
