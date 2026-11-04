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
    from paper_trading.utils import markets

    return markets.exchange_of(symbol)


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


# ---------- T26：每日收盘后积累 + 缺口报告 ----------
#: A 股连续竞价分钟全集，bar 以**右端时刻**标记（09:35 = 09:30-09:35 那根）：
#: 上午 09:31..11:30（120 根）、下午 13:01..15:00（120 根）→ 1 分钟 240 根/日。
SESSION_START_MIN = 9 * 60 + 30


def _trading_minutes() -> list[int]:
    return (list(range(9 * 60 + 31, 11 * 60 + 31)) +
            list(range(13 * 60 + 1, 15 * 60 + 1)))


def expected_slots(period: str = "5") -> list[str]:
    """该周期下一交易日应有的 bar 时刻（实测生产库 5 分钟 = 48 根/标的/日）。"""
    step = max(1, int(period))
    out = []
    for m in _trading_minutes():
        if (m - SESSION_START_MIN) % step == 0 or m in (11 * 60 + 30, 15 * 60):
            out.append(f"{m // 60:02d}:{m % 60:02d}")
    return out


def completed_slots(period: str = "5",
                    now: Optional[datetime] = None) -> list[str]:
    """到现在为止**已经收完**的 bar 时刻。

    bar 以右端标记（14:35 那根覆盖 14:30–14:35）⇒ 只有严格早于当前分钟的
    时刻才算收完；把"还在走"的那根当完整 bar 落库，引擎就会拿半个 K 线的
    收盘价撮合（不可归因），所以这里宁可晚一分钟。
    """
    tn = now or datetime.now()
    limit = tn.hour * 60 + tn.minute
    out = []
    for s in expected_slots(period):
        if int(s[:2]) * 60 + int(s[3:5]) < limit:
            out.append(s)
    return out


def gap_fingerprint(day: str, errors) -> str:
    """一次采集的"症状指纹"（同日同指纹只落一条 `op_log[minute:gap]`）。

    盘中采集每 15 分钟一跳，11-03 现网一天刷出 92 条 `minute:gap`：
    刷屏的告警会把真故障埋掉（T22 看门狗"只在状态翻转时落流水"同一精神）。
    症状**集合变化**（新标的坏了 / 修好了又坏）仍然立刻留痕。
    """
    return f"{day}|" + ",".join(sorted(str(e) for e in (errors or [])))


def latest_expected_ts(day: str, period: str = "5",
                       now: Optional[datetime] = None) -> Optional[str]:
    """到此刻为止**应该已经有了**的最后一根 bar 时刻（午休/盘前都算得对）。

    11-03 现网 12:09 的误报就是缺这个：A 股 11:30 收午盘，`minute_bars` 的最新
    一根停在 11:30 是**正确**的，但按"墙钟距今 > 20 分钟"判陈旧就会红——整个午休
    每小时响一次、午后立刻再响，报警一失真就没人看（纪律 4 的反面）。
    """
    done = completed_slots(period, now)
    return f"{day}T{done[-1]}" if done else None


def ensure_today_minutes(db, symbols, *, day: Optional[str] = None,
                         period: str = "5", now: Optional[datetime] = None,
                         fetch=None, gap: float = GAP_SEC,
                         days: int = 3) -> dict:
    """盘中把当日**已收完**的分钟线补进库（新线撮合的命脉供给）。

    为什么需要它：切换后判定侧每分钟跑，撮合侧每 5 分钟才跳一次，而
    `minute_bars` 里没有任何 cron 在盘中喂数据——旧线只是 14:xx 靠实时报价
    直接成交，新线 14:xx 必须在库里找那根 bar，找不到就排队、隔日过期
    （= 静默丢机会，纪律 4 要禁止的那种）。

    只写已收完的 bar；只拉缺的标的（库里已齐的标的不重复打接口）；
    每只的 written/latest/missing/error 如实返回，调用方据此报警，不静默。
    """
    tn = now or datetime.now()
    day = day or tn.date().isoformat()
    if fetch is None:
        def fetch(s, p, d, n):
            return fetch_minute(s, period=p, days=d, now=n)
    want = completed_slots(period, tn)
    want_set = set(want)
    per_symbol: dict[str, dict] = {}
    for i, sym in enumerate(symbols or []):
        try:
            have = {str(t)[:16] for t in db.minute_ts_on(sym, day, period=period)}
        except Exception as exc:                    # noqa: BLE001 - 读库失败如实带出
            per_symbol[sym] = {"written": 0, "latest": "", "missing": list(want),
                               "error": f"{type(exc).__name__}: {str(exc)[:60]}"}
            continue
        missing = [s for s in want if f"{day}T{s}" not in have]
        if not missing:
            per_symbol[sym] = {"written": 0, "latest": (f"{day}T{want[-1]}"
                                                        if want else ""),
                               "missing": []}
            continue
        try:
            rows = fetch(sym, period, days, tn) or []
        except Exception as exc:                    # noqa: BLE001
            per_symbol[sym] = {"written": 0, "latest": "", "missing": missing,
                               "error": f"{type(exc).__name__}: {str(exc)[:60]}"}
            if i + 1 < len(symbols) and gap > 0:
                time.sleep(gap)
            continue
        # 只留：今天 + 已收完的时刻（源可能把在走的那根也吐出来）
        keep = [r for r in rows
                if str(r.get("ts", ""))[:10] == day
                and str(r.get("ts", ""))[:16] in {f"{day}T{s}" for s in want_set}]
        written = db.upsert_minute_bars(sym, keep, period=period) if keep else 0
        have2 = {str(t)[:16] for t in db.minute_ts_on(sym, day, period=period)}
        still = [s for s in want if f"{day}T{s}" not in have2]
        per_symbol[sym] = {"written": written,
                           "latest": (max(have2)[0:16] if have2 else ""),
                           "missing": still,
                           "error": "" if not still
                           else f"源无这些 bar：{','.join(still[-3:])}"}
        if i + 1 < len(symbols) and gap > 0:
            time.sleep(gap)
    errs = sorted(s for s, v in per_symbol.items() if v.get("error"))
    return {"day": day, "period": str(period), "symbols": len(per_symbol),
            "written": sum(v["written"] for v in per_symbol.values()),
            "needed": any(v.get("missing") or v.get("written") for v in
                          per_symbol.values()),
            "errors": errs, "per_symbol": per_symbol}


def sync_day(db, symbols, *, day: Optional[str] = None, period: str = "5",
             days: int = MAX_DAYS, fetch=None, now: Optional[datetime] = None,
             gap: float = GAP_SEC) -> dict:
    """把某交易日的分钟线落库（幂等 upsert）并给出**缺口报告**。

    `fetch(symbol, period, days, now) -> list[dict]` 可注入（测试用假源）。
    数据现实：新浪 N 分钟只给最近约 22 个交易日，更早的缺口**补不回来**，
    所以这里只报告、不假装能回填（AGENTS 纪律 4 不静默）。
    """
    now = now or datetime.now()
    day = day or now.date().isoformat()
    if fetch is None:
        def fetch(s, p, d, n):
            return fetch_minute(s, period=p, days=d, now=n)

    written = 0
    per_symbol: dict[str, dict] = {}
    for i, sym in enumerate(symbols or []):
        try:
            rows = fetch(sym, period, days, now) or []
        except Exception as exc:            # noqa: BLE001 - 单只失败不影响整批
            per_symbol[sym] = {"written": 0, "error": f"{type(exc).__name__}: {str(exc)[:80]}"}
            continue
        day_rows = [r for r in rows if str(r.get("ts", ""))[:10] == day]
        n = db.upsert_minute_bars(sym, day_rows, period=period) if day_rows else 0
        written += n
        have = db.minute_ts_on(sym, day, period=period)
        missing = [f"{day}T{s}" for s in expected_slots(period)
                   if f"{day}T{s}" not in have]
        per_symbol[sym] = {"written": n, "bars": len(have), "missing": len(missing),
                           "missing_sample": missing[:6]}
        if i + 1 < len(symbols) and gap > 0:
            time.sleep(gap)
    return {"day": day, "period": str(period), "symbols": len(symbols or []),
            "written": written,
            "expected_per_symbol": len(expected_slots(period)),
            "per_symbol": per_symbol,
            "no_data": sorted(s for s, v in per_symbol.items() if not v.get("written")),
            "gapped": sorted(s for s, v in per_symbol.items() if v.get("missing")),
            "errors": sorted(s for s, v in per_symbol.items() if v.get("error"))}
