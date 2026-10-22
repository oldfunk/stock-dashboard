"""1 分钟 K 线采集（盘中量化用）：新浪 N 分钟 K 线 + 腾讯分时。

为什么不用东财：东财 1 分钟接口在部分网络环境（如 Pi）会直接断连，
实测 fetch 全部失败（0 行），而"看起来成功、其实没数据"是最坏的一种失败。
所以这里用**实测可用**的源：
- 新浪 N 分钟 K 线（默认 5 分钟）：多日历史（实测 22 个交易日）、真实 OHLC，
  但**没有成交额** → 成交额用「典型价 × 成交量」估算（VWAP 权重够用）
- 腾讯分时：今日 1 分钟，带**真实累计成交额** → 只在显式要 1 分钟时用

- 标准库实现，无需新依赖；任何源失败都退化为空列表（永不抛错阻断主流程）
- 拉取需礼貌节流：连续多只按 GAP 秒间隔
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from paper_trading.utils import get_logger

logger = get_logger(__name__)

GAP_SEC = 0.6          # 连续拉取间隔（礼貌节流）
MAX_DAYS = 30          # 新浪可取的历史长度（按 N 分钟折算交易日）
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _f(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def _market_prefix(symbol: str) -> str:
    """6 位代码 → 交易所前缀（sh/sz/bj）。"""
    c = str(symbol or "").strip()
    if len(c) != 6 or not c.isdigit():
        return "sh"
    if c[0] == "6":
        return "sh"
    if c[0] in "03":
        return "sz"
    if c[0] in "48":
        return "bj"
    return "sh"


def _http_get(url: str, timeout: int = 20,
              headers: Optional[dict] = None) -> str:
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


# ---------- 新浪 N 分钟 K 线（多日历史） ----------

_SINA_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "CN_MarketData.getKLineData?symbol={code}&scale={scale}&ma=no&datalen={n}")


def fetch_minute_sina(symbol: str, scale: int = 5, datalen: int = 1023,
                      now: Optional[datetime] = None) -> list[dict]:
    """新浪 N 分钟 K 线（真实 OHLC，无成交额 → 典型价估算）。

    返回 [{ts, open, high, low, close, volume, amount}]，按时间升序。
    """
    code = f"{_market_prefix(symbol)}{symbol}"
    try:
        txt = _http_get(_SINA_URL.format(code=code, scale=int(scale),
                                         n=int(datalen)))
    except Exception as e:      # noqa: BLE001 - 网络/限流都退化为空
        logger.warning(f"sina minute failed for {symbol}: {type(e).__name__}: {e}")
        return []
    txt = (txt or "").strip()
    if not txt or txt in ("null", "[]"):
        return []
    try:
        arr = json.loads(txt)
    except ValueError:
        return []
    if not isinstance(arr, list):
        return []
    out: list[dict] = []
    for r in arr:
        if not isinstance(r, dict):
            continue
        ts = str(r.get("day") or "").strip().replace(" ", "T")[:16]
        if len(ts) < 16:
            continue
        o = _f(r.get("open"))
        h = _f(r.get("high"))
        l = _f(r.get("low"))
        c = _f(r.get("close"))
        vol = _f(r.get("volume"))
        if c <= 0:
            continue
        typical = (h + l + c) / 3.0 if h > 0 and l > 0 else c
        out.append({"ts": ts, "open": o, "close": c, "high": h, "low": l,
                    "volume": vol, "amount": round(typical * vol, 2)})
    out.sort(key=lambda x: x["ts"])
    logger.info(f"Fetched {len(out)} {scale}m bars for {symbol} (sina)")
    return out


# ---------- 腾讯分时（今日 1 分钟，真实成交额） ----------

_TENCENT_URL = ("https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}")


def fetch_minute_tencent(symbol: str, now: Optional[datetime] = None) -> list[dict]:
    """腾讯分时：今日 1 分钟（真实成交额，VWAP 权重最准）。

    原始行格式："HHMM 价格 累计成交量(手) 累计成交额(元)"，
    这里换算成每分钟的增量成交量(股)与成交额(元)。
    """
    code = f"{_market_prefix(symbol)}{symbol}"
    try:
        txt = _http_get(_TENCENT_URL.format(code=code),
                        headers={"User-Agent": UA["User-Agent"],
                                 "Referer": "https://gu.qq.com/"})
        obj = json.loads(txt)
    except Exception as e:      # noqa: BLE001
        logger.warning(f"tencent minute failed for {symbol}: {type(e).__name__}: {e}")
        return []
    try:
        node = obj.get("data", {}).get(code, {}).get("data", {})
        date = str(node.get("date") or "")
        rows = node.get("data") or []
    except Exception:           # noqa: BLE001
        return []
    if not date or not rows:
        return []
    day = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
    out: list[dict] = []
    prev_vol = prev_amt = 0.0
    for line in rows:
        parts = str(line).split()
        if len(parts) < 3:
            continue
        hm, price = parts[0], _f(parts[1])
        cum_vol = _f(parts[2]) if len(parts) > 2 else 0.0
        cum_amt = _f(parts[3]) if len(parts) > 3 else 0.0
        if len(hm) < 4 or price <= 0:
            continue
        hh, mm = int(hm[:2]), int(hm[2:4])
        if hh > 23 or mm > 59:
            continue
        # 成交量单位是手 → 换算成股；成交额是元
        vol = max(0.0, (cum_vol - prev_vol)) * 100.0
        amt = max(0.0, cum_amt - prev_amt)
        prev_vol, prev_amt = cum_vol, cum_amt
        ts = f"{day}T{hh:02d}:{mm:02d}"
        out.append({"ts": ts, "open": price, "close": price, "high": price,
                    "low": price, "volume": vol, "amount": amt})
    out.sort(key=lambda x: x["ts"])
    logger.info(f"Fetched {len(out)} 1m bars for {symbol} (tencent)")
    return out


# ---------- 合并入口 ----------

def fetch_minute(symbol: str, period: str = "5", days: int = MAX_DAYS,
                 now: Optional[datetime] = None) -> list[dict]:
    """拉单只的分钟线（默认 5 分钟 K 线，新浪源）；失败返回 []。

    刻意**不混用**不同粒度：同一张表里混着 1 分钟和 5 分钟会让均线类规则
    前后不可比。要 1 分钟精度时显式传 period=1（当天才有数据）。
    """
    now = now or datetime.now()
    scale = 5
    if str(period) in ("1", "5", "15", "30", "60"):
        scale = int(period)
    rows = fetch_minute_sina(symbol, scale=scale,
                             datalen=max(60, int(days) * 48 if scale >= 5 else 240),
                             now=now)
    if scale == 1:
        # 1 分钟：新浪没有，只有腾讯分时（仅当日）→ 用它补今天的部分
        today = now.date().isoformat()
        rows = [r for r in rows if not str(r["ts"]).startswith(today)]
        rows.extend(fetch_minute_tencent(symbol, now=now))
    # 只保留最近 days 天
    cut = (now - timedelta(days=int(days) + 1)).date().isoformat()
    rows = [r for r in rows if str(r["ts"])[:10] >= cut]
    rows.sort(key=lambda x: x["ts"])
    return rows


def fetch_minute_batch(symbols: list[str], period: str = "1",
                       days: int = MAX_DAYS, gap: float = GAP_SEC,
                       now: Optional[datetime] = None) -> dict[str, int]:
    """批量拉分钟线（节流），返回 {symbol: 行数}。"""
    out: dict[str, int] = {}
    for i, sym in enumerate(symbols or []):
        rows = fetch_minute(sym, period=period, days=days, now=now)
        out[sym] = len(rows)
        if i + 1 < len(symbols) and gap > 0:
            time.sleep(gap)
    return out
