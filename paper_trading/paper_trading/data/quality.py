"""行情数据质量守卫（M2，docs/ROADMAP.md §2 第 1 项）。

四类发现 + 同步失败可见，全部显式结构化（**不静默**）：

- ``no_data``        同步名单里的标的本地一根 K 线都没有（同步失败/下架）
- ``duplicate_date`` 同一交易日多条记录（timestamp 形态差异绕过 (symbol,timestamp) 主键）
- ``bad_price``      收盘价 ≤0 或非有限（脏数据不得进信号/计价）
- ``absurd_move``     相邻交易日 |涨跌幅| > 31%（超过北交所 30% 涨跌停带，
  A 股真实行情不可能——多为复权口径混用/取错列/回填错误；本地为前复权，
  除权后历史已回溯重写，正常 qfq 序列的除权日不会出现此类跳变）
- ``gap``            区间**中段**缺失交易日（按交易日历判断；停牌段也在此暴露）
- ``stale``          最后一根 K 线距今 ≥ N 个交易日（长期停牌/同步停滞标记；
  阈值内属正常停牌，是"标记"不是"报错"）

出口：``sync_data`` 落 ``op_log[data:quality]`` + 返回值 ``quality`` 字段；
CLI ``python -m paper_trading.cli data quality``。守卫自身异常绝不阻断同步。
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Optional

#: 相邻日涨跌幅绝对值上限：北交所 30% 是全市场最宽档，留 1pp 容差
ABS_MOVE_LIMIT = 0.31
#: 最后一根 K 线落后多少交易日算"长期停牌/同步停滞"标记
SUSPENSION_TRADING_DAYS = 10
#: 新股上市宽限天数（M-3）：创业板/科创板上市前 5 日无涨跌幅、主板首日
#: +44% 上限——上市初期的大幅波动是合法行情，不许判成脏数据
NEW_LISTING_GRACE_DAYS = 10
#: 缺口扫描最大迭代天数（防日历异常导致死循环）
_MAX_GAP_SCAN = 200


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)[:10]).date()


def _trading_days_between(a: date, b: date) -> list[date]:
    """(a, b] 区间内的交易日（周末+本地日历缓存判断；仅用于质量检查）。"""
    from paper_trading.utils.trading_calendar import is_trading_day

    out: list[date] = []
    d, n = a, 0
    while d < b and n < _MAX_GAP_SCAN:
        d += timedelta(days=1)
        n += 1
        if is_trading_day(d):
            out.append(d)
    return out


def check_symbol(db, symbol: str, *, days: int = 30,
                 today: Optional[date] = None,
                 abs_move_limit: float = ABS_MOVE_LIMIT,
                 suspension_days: int = SUSPENSION_TRADING_DAYS) -> list[dict]:
    """单标的质量检查，返回发现列表（空 = 干净）。"""
    today = _as_date(today or date.today())
    # 多取一倍余量：中段缺口扫描要覆盖整个区间（升序返回）
    bars = db.get_bars(symbol, limit=max(int(days) * 2, 30))
    if not bars:
        return [{"type": "no_data", "symbol": symbol,
                 "detail": "本地无任何 K 线（同步失败或标的无效）"}]

    findings: list[dict] = []
    by_date: dict[date, list] = {}
    ordered: list[tuple[date, float]] = []
    for b in bars:
        d = _as_date(b.timestamp)
        by_date.setdefault(d, []).append(b)
        ordered.append((d, float(b.close)))
    ordered.sort(key=lambda x: x[0])

    # 1) 同日重复
    dups = sorted(d for d, rows in by_date.items() if len(rows) > 1)
    for d in dups[:10]:
        findings.append({"type": "duplicate_date", "symbol": symbol,
                         "date": d.isoformat(),
                         "detail": f"同日 {len(by_date[d])} 条记录"
                                   f"（timestamp 形态不一致绕过主键）"})

    # 2) 非法价格
    for d, c in ordered:
        if not math.isfinite(c) or c <= 0:
            findings.append({"type": "bad_price", "symbol": symbol,
                             "date": d.isoformat(),
                             "detail": f"收盘价非法：{c!r}"})
    # 3) 异常涨跌幅（只看相邻**不同交易日**，非法价已在上面单列）
    #    M-3（Qoder 审查）：上市初期宽限——首根 K 线起 10 天内的大幅波动
    #    是合法行情（创业板/科创前 5 日无涨跌幅），不判脏数据。
    first_d = ordered[0][0]
    prev_d: Optional[date] = None
    prev_c: Optional[float] = None
    for d, c in ordered:
        if prev_d is not None and d != prev_d and prev_c and prev_c > 0 \
                and math.isfinite(c):
            pct = c / prev_c - 1.0
            if abs(pct) > abs_move_limit \
                    and (d - first_d).days > NEW_LISTING_GRACE_DAYS:
                findings.append({"type": "absurd_move", "symbol": symbol,
                                 "date": d.isoformat(),
                                 "detail": f"相邻日涨跌 {pct:+.1%}（超 ±31% 涨跌"
                                           f"停带，疑口径混用/取错列）",
                                 "pct": round(pct, 4)})
        prev_d, prev_c = d, c

    # 4) 中段缺口（首尾之间缺失的交易日）
    min_d, max_d = min(by_date), max(by_date)
    present = set(by_date)
    missing = [d.isoformat() for d in _trading_days_between(min_d, max_d)
               if d not in present]
    if missing:
        findings.append({"type": "gap", "symbol": symbol,
                         "detail": f"区间 {min_d}~{max_d} 缺 {len(missing)} 个交易日",
                         "missing": missing[:20]})

    # 5) 末尾停滞（长期停牌/同步停滞标记）
    if max_d < today:
        lag = len(_trading_days_between(max_d, today))
        if lag >= suspension_days:
            findings.append({"type": "stale", "symbol": symbol,
                             "date": max_d.isoformat(),
                             "detail": f"已 {lag} 个交易日无新 K 线"
                                       f"（≥{suspension_days}，疑似长期停牌/同步停滞）",
                             "lag_trading_days": lag})
    return findings


def check(db, symbols, *, days: int = 30, today: Optional[date] = None,
          **kw) -> dict:
    """批量质量检查。

    返回 ``{"checked", "days", "clean", "n_findings", "findings": {sym: [...]}}``；
    ``clean=True`` 表示所有标的零发现。
    """
    syms = list(dict.fromkeys(str(s) for s in (symbols or [])))
    findings: dict[str, list] = {}
    for s in syms:
        try:
            f = check_symbol(db, s, days=days, today=today, **kw)
        except Exception as e:               # noqa: BLE001 - 单票异常不阻断
            f = [{"type": "check_error", "symbol": s,
                  "detail": f"{type(e).__name__}: {e}"}]
        if f:
            findings[s] = f
    n = sum(len(v) for v in findings.values())
    return {"checked": len(syms), "days": int(days), "clean": n == 0,
            "n_findings": n, "findings": findings}
