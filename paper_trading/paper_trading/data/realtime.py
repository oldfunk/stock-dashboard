"""腾讯实时行情（与 Stock Dashboard 同一数据源：qt.gtimg.cn，批量一次拉全池）。

- 标准库实现，无额外依赖；失败一律回空，永不阻断主流程
- 仅交易日连续竞价时段拉取（09:30-11:30 / 13:00-15:00，UTC+8），其余返回空
  （例外：`get_quotes(..., allow_closing=True)` 为**盘后跨源对照**放行收盘快照，T50-A）
- 解析行情时间戳：日期不是当天或距当前超过 QUOTE_MAX_AGE 的报价直接丢弃
  （节假日/午休/停牌返回的旧报价不会进入撮合与风控）
- 用途：盘中展示现价 + LLM 上下文盘中快照 + 盘后当日跨源对照；撮合/NAV 仍用收盘定稿口径
"""
from __future__ import annotations

import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from paper_trading.utils.market_time import is_trading_session as _is_trading_session

CN = timezone(timedelta(hours=8))
QUOTE_TTL = 60.0
QUOTE_MAX_AGE = 180.0        # 报价距当前超过 3 分钟视为过期（秒）
#: 收盘快照可用窗口（秒）：T50 裁决 1-A。实测 18:22 拉到的当日快照
#: 时间戳是 16:14（腾讯收盘后才定稿），时龄两小时——180 秒那套是**盘中撮合**的
#: 规矩，拿来管"收盘后拿当日价做跨源对照"就是把 A 防线自己关掉（report-t42 §4）。
#: 所以另开一条：必须是**当天**、ts ≥ 15:00（收盘之后）、且不超过这个窗口（当晚有效，
#: 次日清晨自动作废，不会拿昨天的收盘快照冒充今天）。
QUOTE_CLOSING_MAX_AGE = 9 * 3600.0
CLOSE_MINUTE = 15 * 60


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
    """6 位代码 → 腾讯行情码（`sh600519`/`sz000858`/`bj920156`）。

    口径唯一来源是 `utils/markets.py`（T60）。旧写法"只 8/4 开头判 bj、其余 sz"
    沿用的是母库 `tc_encode`，但 2026-11-03 现网实测：`sz920156` 腾讯回 1 个字段
    （空），`bj920156` 回 87 个字段（海昌智能 36.10）⇒ 920 段必须判北交所，
    否则该标的**永远没有报价**。**母库那一版同样需要改**（转 stock-dashboard）。
    """
    from paper_trading.utils import markets

    return markets.sina_symbol(code)


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


def _quote_dt(quote: dict) -> Optional[datetime]:
    """把报价的 `ts` 解析成北京时间；无时间戳/解析不了返回 None。"""
    ts = str((quote or {}).get("ts") or "")
    if not ts:
        return None
    try:
        qt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if qt.tzinfo is None:
        qt = qt.replace(tzinfo=CN)
    return qt.astimezone(CN)


def is_closing_snapshot(quote: dict, now: Optional[datetime] = None) -> bool:
    """这是**今天收盘之后**的定稿快照（T50-A 的判据）。

    三条都要满足：日期是当天、时刻 ≥ 15:00（收盘及之后，才叫收盘快照）、
    时龄不超过 `QUOTE_CLOSING_MAX_AGE`（过了当晚就作废，宁可回到"没有参照"，
    也不能拿昨天的收盘价冒充今天的独立参照）。
    """
    qt = _quote_dt(quote)
    if qt is None:
        return False
    t = (now or datetime.now(CN)).astimezone(CN)
    if qt.date() != t.date():
        return False
    if qt.hour * 60 + qt.minute < CLOSE_MINUTE:
        return False
    return 0.0 <= (t - qt).total_seconds() <= QUOTE_CLOSING_MAX_AGE


def quote_usable(quote: dict, now: Optional[datetime] = None, *,
                 allow_closing: bool = False) -> bool:
    """撮合/风控口径（`quote_fresh`）不变；只有显式 `allow_closing` 才放宽到收盘快照。"""
    if quote_fresh(quote, now):
        return True
    return bool(allow_closing) and is_closing_snapshot(quote, now)


def get_quotes(codes: list[str], force: bool = False,
               allow_closing: bool = False,
               now: Optional[datetime] = None) -> dict[str, dict]:
    """带 60s 缓存的拉取入口；非交易时段直接回空；过期报价一律过滤。

    `allow_closing=True` 是给**盘后跨源对照**用的（T50 裁决 1-A）：
    收盘后照样拉取，并按"当天 + ts≥15:00 + 时龄在窗口内"放行。
    撮合与风控的调用点**不要**传这个参数——那条路径的新鲜度规矩一字未改。
    `now` 是时钟注入点（测试/回填用），默认取北京时间现在。
    """
    global _cache
    epoch = time.time()
    tnow = (now or datetime.now(CN)).astimezone(CN)
    if not force and (epoch - _cache["at"] < QUOTE_TTL):
        data = _cache["data"]
    else:
        if not is_trading_session(tnow) and not allow_closing:
            return {}
        data = fetch_batch(codes)
        _cache = {"at": epoch, "data": data}
    # 新鲜度过滤对缓存同样生效：午休/节假日缓存里的旧报价不得进入撮合
    return {c: data[c] for c in codes
            if c in data and quote_usable(data[c], tnow, allow_closing=allow_closing)}
