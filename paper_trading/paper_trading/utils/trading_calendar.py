"""交易日历工具（A股：跳周末 + 可配置节假日）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Set


def as_date(d: date | datetime | str) -> date:
    """归一化为 date（接受 date/datetime/ISO 字符串）。"""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.fromisoformat(d[:10]).date()
    return d


_as_date = as_date  # 内部别名，保持兼容


def is_trading_day(d: date | datetime | str, holidays: Optional[Set[str]] = None) -> bool:
    """是否为交易日：周一~周五且不在 holidays(YYYY-MM-DD集合)内。"""
    dd = _as_date(d)
    if dd.weekday() >= 5:
        return False
    if holidays and dd.isoformat() in holidays:
        return False
    return True


def next_trading_day(
    d: date | datetime | str, holidays: Optional[Set[str]] = None, steps: int = 1
) -> date:
    """下一个交易日（steps=1 即 T+1 解冻日）。"""
    dd = _as_date(d)
    n = 0
    while n < steps:
        dd += timedelta(days=1)
        if is_trading_day(dd, holidays):
            n += 1
    return dd


def load_holidays(extra: Optional[Iterable[str]] = None) -> Set[str]:
    """合并外部节假日列表为集合，容错空值。"""
    out: Set[str] = set()
    if extra:
        for h in extra:
            h = str(h).strip()[:10]
            if h:
                out.add(h)
    return out
