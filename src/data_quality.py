"""数据质量只读诊断（2026-11-28）。

不修数据、不回填、不改筛选；只回答：
1. 分析记录与 outcome 的月度覆盖（缺口在哪个月）；
2. `market_state_daily` 指标缺失（如 pe_percentile_5y）与重复；
3. `daily_close` 相对交易日的覆盖缺口；
4. 财务字段（roic/fcf）覆盖率。
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional


def ratio(n: int, d: int) -> Optional[float]:
    return (n / d) if d else None


def month_range(start: str, end: str) -> list[str]:
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def missing_months(present: Iterable[str], start: str, end: str) -> list[str]:
    have = set(present)
    return [m for m in month_range(start, end) if m not in have]


def months_between(dates: Iterable[str]) -> tuple[str, str]:
    vals = sorted(d for d in dates if d)
    return (vals[0], vals[-1]) if vals else ("", "")


def daily_gaps(open_dates: list[str], counts: dict[str, int],
               expect: int, threshold: float = 0.98) -> list[dict]:
    out = []
    for d in open_dates:
        n = int(counts.get(d, 0))
        r = ratio(n, expect)
        if r is None or r < threshold:
            out.append({"date": d, "rows": n, "expect": expect,
                        "ratio": r})
    return out
