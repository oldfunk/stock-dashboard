"""腾讯实时行情（与 Stock Dashboard 同一数据源：qt.gtimg.cn，批量一次拉全池）。

- 标准库实现，无额外依赖；失败一律回空，永不阻断主流程
- 仅交易时段拉取（9:15-15:05，周一至周五，UTC+8），其余时间直接返回空
- 用途：盘中展示现价 + LLM 上下文盘中快照；撮合/NAV 仍用收盘定稿口径
"""
from __future__ import annotations

import time
import urllib.request
from datetime import datetime, timedelta, timezone

CN = timezone(timedelta(hours=8))
QUOTE_TTL = 60.0

_cache: dict = {"at": 0.0, "data": {}}


def is_trading_session(now: datetime | None = None) -> bool:
    """是否在盘中拉取窗口内（含集合竞价与收盘 settling 缓冲）。"""
    t = (now or datetime.now(CN)).astimezone(CN)
    if t.weekday() >= 5:
        return False
    hm = t.hour * 60 + t.minute
    return 9 * 60 + 15 <= hm <= 15 * 60 + 5


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
        return {
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
    """带 60s 缓存的拉取入口；非交易时段直接回空。"""
    global _cache
    now = time.time()
    if not force and (now - _cache["at"] < QUOTE_TTL):
        data = _cache["data"]
        return {c: data[c] for c in codes if c in data}
    if not is_trading_session():
        return {}
    data = fetch_batch(codes)
    _cache = {"at": now, "data": data}
    return data
