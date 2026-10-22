"""腾讯实时行情（与 Stock Dashboard 同一数据源：qt.gtimg.cn，批量一次拉全池）。

- 标准库实现，无额外依赖；失败一律回空，永不阻断主流程
- 仅交易日连续竞价时段拉取（09:30-11:30 / 13:00-15:00，UTC+8），其余返回空
- 解析行情时间戳：日期不是当天或距当前超过 QUOTE_MAX_AGE 的报价直接丢弃
  （节假日/午休/停牌返回的旧报价不会进入撮合与风控）
- 用途：盘中展示现价 + LLM 上下文盘中快照；撮合/NAV 仍用收盘定稿口径
"""
from __future__ import annotations

import time
import urllib.request
from datetime import datetime, timedelta, timezone

from paper_trading.utils.market_time import is_trading_session as _is_trading_session

CN = timezone(timedelta(hours=8))
QUOTE_TTL = 60.0
QUOTE_MAX_AGE = 180.0        # 报价距当前超过 3 分钟视为过期（秒）


_cache: dict = {"at": 0.0, "data": {}}


def is_trading_session(now: datetime | None = None,
                       holidays: set[str] | None = None) -> bool:
    """是否在连续竞价时段内：交易日 09:30-11:30 / 13:00-15:00（UTC+8）。

    交易日由 `utils.trading_calendar` 的缓存判断（周末/节假日），缓存不可用时
    回退为工作日；节假日/延迟更新的旧报价仍由 `quote_fresh` 二次过滤。
    """
    return _is_trading_session(now, holidays=holidays)


def quote_fresh(quote: dict, now: datetime | None = None) -> bool:
    """报价时间戳新鲜度：当天 + 距当前不超过 QUOTE_MAX_AGE。

    没有时间戳的合成数据（单测/旧格式）一律放行，避免误杀。
    """
    ts = str(quote.get("ts") or "")
    if not ts:
        return True
    t = (now or datetime.now(CN)).astimezone(CN)
    try:
        qt = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if qt.tzinfo is None:
        qt = qt.replace(tzinfo=CN)
    qt = qt.astimezone(CN)
    if qt.date() != t.date():
        return False
    age = (t - qt).total_seconds()
    return -10.0 <= age <= QUOTE_MAX_AGE


def tc_symbol(code: str) -> str:
    """6→sh，8/4开头→bj，其余sz（与 Stock Dashboard tc_encode 口径一致）。"""
    c = str(code).strip()
    if c.startswith("6"):
        return "sh" + c
    if c.startswith("8") or c.startswith("4"):
        return "bj" + c
    return "sz" + c


def _f(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if v == v else default  # NaN 剔除
    except (TypeError, ValueError):
        return default


def parse_line(raw: str) -> dict | None:
    """解析单行 v_sz000858="..." 内串（字段下标与 Stock Dashboard parse_tc_line 一致）。"""
    try:
        q1 = raw.index('"')
        q2 = raw.rindex('"')
        parts = raw[q1 + 1:q2].split("~")
        if len(parts) < 50:
            return None
        code = parts[2].strip()
        if not code:
            return None
        out = {
            "code": code,
            "name": parts[1].strip(),
            "price": _f(parts[3]),
            "prev_close": _f(parts[4]),
            "open": _f(parts[5]),
            "volume": _f(parts[6]),
            "high": _f(parts[33]),
            "low": _f(parts[34]),
            "change_pct": _f(parts[32]),
            "turnover": _f(parts[38]) if len(parts) > 38 else 0.0,
        }
        # 行情时间戳（腾讯字段下标 30，YYYYMMDDHHMMSS）：解析成功才带 ts/date
        raw_ts = parts[30].strip() if len(parts) > 30 else ""
        if len(raw_ts) >= 14 and raw_ts[:14].isdigit():
            try:
                qt = datetime.strptime(raw_ts[:14], "%Y%m%d%H%M%S").replace(tzinfo=CN)
                out["ts"] = qt.isoformat()
                out["date"] = qt.date().isoformat()
            except ValueError:
                pass
        return out
    except Exception:
        return None


def fetch_batch(codes: list[str], timeout: float = 15.0) -> dict[str, dict]:
    """批量拉取（80 只一批，批次间隔 0.3s，礼貌爬取）。"""
    codes = [str(c).strip() for c in codes if str(c).strip()]
    if not codes:
        return {}
    out: dict[str, dict] = {}
    tc = [tc_symbol(c) for c in codes]
    for i in range(0, len(tc), 80):
        batch = tc[i:i + 80]
        try:
            req = urllib.request.Request(
                "https://qt.gtimg.cn/q=" + ",".join(batch),
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("gbk", "ignore")
        except Exception:
            continue
        for chunk in text.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            q = parse_line(chunk)
            if q:
                out[q["code"]] = q
        if i + 80 < len(tc):
            time.sleep(0.3)
    return out


def get_quotes(codes: list[str], force: bool = False) -> dict[str, dict]:
    """带 60s 缓存的拉取入口；非交易时段直接回空；过期报价一律过滤。"""
    global _cache
    now = time.time()
    tnow = datetime.now(CN)
    if not force and (now - _cache["at"] < QUOTE_TTL):
        data = _cache["data"]
    else:
        if not is_trading_session(tnow):
            return {}
        data = fetch_batch(codes)
        _cache = {"at": now, "data": data}
    # 新鲜度过滤对缓存同样生效：午休/节假日缓存里的旧报价不得进入撮合
    return {c: data[c] for c in codes
            if c in data and quote_fresh(data[c], tnow)}
