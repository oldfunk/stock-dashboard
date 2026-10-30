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
import re
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


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 列类型允许 `TEXT` / `REAL` / `VARCHAR(20)` / `INTEGER NOT NULL` 这类形态，
# 但绝不允许引号、分号、注释符——它是 SQL 里第三个字面量插值位
_COLTYPE_RE = re.compile(r"^[A-Za-z0-9_]+(\([0-9]+\))?( [A-Za-z0-9_]+)*$")


def safe_identifier(name, what: str = "标识符") -> str:
    """白名单校验 SQL 标识符（表名/列名）。

    这些位置历史上用 f-string 直接拼进 SQL（审计 P1-2）。当前全部调用方传的都是
    代码内常量，所以今天不是可利用漏洞；但 `PRAGMA table_info({table})` 一类
    一旦有外部来源（例如 manifest / 配置文件里的表名）就是注入入口。
    白名单优于转义：标识符不能加引号包起来安全化（PRAGMA 不接受带引号的表名）。
    """
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"非法{what}: {name!r}")
    return name


def safe_col_type(col_type: str) -> str:
    """白名单校验列类型串（同 P1-2，防 ALTER TABLE 拼接位被塞进分号/注释）。"""
    if not isinstance(col_type, str) or not _COLTYPE_RE.match(col_type.strip()):
        raise ValueError(f"非法列类型: {col_type!r}")
    return col_type.strip()


def sql_quote(value) -> str:
    """SQL 单引号字面量转义（DuckDB/SQLite 同规则：' → ''）。

    用于必须把路径当字面量塞进语句的场合，如 read_parquet('...')。
    """
    return str(value).replace("'", "''")


def safe_float(val, nd: Optional[int] = 2) -> Optional[float]:
    """安全浮点转换：过滤 None/NaN/Inf。

    `nd` = 保留的小数位，默认 2（股价/金额类够用，A 股最小报价单位 0.01 元）；
    **每股指标（EPS、每股现金流）必须传 `nd=None` 取全精度**——EPS 常在 0.0x 量级，
    四舍五入到 2 位的相对误差可达两位数百分比，而下游要用 `净利润 / EPS` 反推
    总股本、算 PE（审计 N22）。
    """
    if val is None:
        return None
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        return v if nd is None else round(v, nd)
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


def market_of(code: str) -> str:
    """6 位股票代码 -> 交易所代号：``BJ``（北交所）/ ``SH`` / ``SZ``。

    三分法（批12-a，Issue #9）。旧实现全站都是"6/9 开头→沪，其余→深"的二分，
    于是北交所被系统性误映射：新号段 ``920xxxx`` 以 9 开头被当成沪市，
    旧号段 ``83/87/43`` 被当成深市。实测（920000 安徽凤凰）——

    - 东财 datacenter：``SECUCODE="920000.BJ"`` 5 行，``.SH`` / ``.SZ`` 各 **0 行**；
    - 东财 F10：``code=BJ920000`` 日期列表 38 条（年报 14 条），
      ``SH920000`` / ``SZ920000`` **0 条**；
    - 东财 K 线：``secid=0.920000`` 有 1,417 根日线，``secid=1.920000`` **0 根**
      （所以 BJ 走"深圳那一侧"的 secid，不是沪市那一侧）。

    判据来源是号段本身；``900xxx``（沪市 B 股）仍归 SH——它和 ``920`` 不冲突，
    所以这里先判 BJ 再判 SH 的 ``9`` 分支。
    """
    c = (code or "").strip()
    if c.startswith(('920', '83', '87', '43')):
        return 'BJ'
    if c.startswith('6') or c.startswith('9'):
        return 'SH'
    return 'SZ'


def sina_symbol(code: str) -> str:
    """新浪/腾讯行情格式：``sh600519`` / ``sz000001`` / ``bj920000``。"""
    c = (code or '').strip()
    return f"{market_of(c).lower()}{c}"


def em_secid(code: str) -> str:
    """东财 K 线 secid：沪市 ``1.``，深市与**北交所**都是 ``0.``（实测）。"""
    c = (code or '').strip()
    return f"{'1' if market_of(c) == 'SH' else '0'}.{c}"


def em_f10_symbol(code: str) -> str:
    """东财 F10 的 ``code=`` 参数格式：``SH600519`` / ``SZ000001`` / ``BJ920000``。"""
    c = (code or '').strip()
    return f"{market_of(c)}{c}"


def em_secucode(code: str) -> str:
    """东财 datacenter 的 ``SECUCODE`` 格式：``600519.SH`` / ``920000.BJ``。"""
    c = (code or '').strip()
    return f"{c}.{market_of(c)}"


def tc_encode(code: str) -> str:
    """6 位股票代码转腾讯行情格式：600519→sh600519，000807→sz000807，
    900951→sh900951，920000→bj920000（北交所，实测 qt.gtimg.cn 认这个前缀，
    且字段布局与沪深同位：名称=1、代码=2、现价=3、昨收=4、今开=5、时间=30）。"""
    return sina_symbol(code)


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
            # N8①（2026-10-29）：parts[52]=市盈率TTM（实证 600519：动态 20.79
            # / TTM 19.01 / 静态 20.56，语义自洽）。此前该值被丢弃，策略的
            # pe_ttm_max 实际校验的是动态 pe（名不符实）。
            'pe_ttm': safe_float(parts[52]) if len(parts) > 52 else None,
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
