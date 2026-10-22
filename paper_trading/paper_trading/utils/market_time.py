"""A 股交易时间：固定时段与运行窗口的唯一口径。

这里只回答“现在是不是交易时间 / 在哪个窗口”，不读用户队列，也不问模型。
交易时段是交易所规则，写死在代码里：

- 上午 09:30–11:30
- 下午 13:00–15:00

交易日（周末 + 节假日）由 `trading_calendar` 缓存判断；缓存不可用时回退为
“周一~周五”，不会因为日历源临时失败而把周末当交易日。手填的
`config.holidays` 用于 T+1 解冻/计划目标日；盘中报价另有“必须是当天”
的二次过滤，即使日历缓存临时缺失也不会拿隔夜旧价撮合。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .trading_calendar import is_trading_day

CN = timezone(timedelta(hours=8))

AM_START = 9 * 60 + 30
AM_END = 11 * 60 + 30
PM_START = 13 * 60
PM_END = 15 * 60

# 开盘执行计划：只给开盘后一段容错时间，避免午后才追旧计划。
OPEN_EXEC_WINDOW = (9 * 60 + 30, 10 * 60)
# 收盘后做计划：数据定稿后开始，留足分析时间；没定稿就下个 tick 重试。
PLAN_WINDOW = (16 * 60 + 30, 18 * 60 + 30)


def now_cn(now: Optional[datetime] = None) -> datetime:
    """统一成 UTC+8；naive 时间按本机 A 股时区解释。"""
    if now is None:
        return datetime.now(CN)
    if now.tzinfo is None:
        return now.replace(tzinfo=CN)
    return now.astimezone(CN)


def _minutes(t: datetime) -> int:
    return t.hour * 60 + t.minute


def _in_window(t: datetime, window: tuple[int, int]) -> bool:
    return window[0] <= _minutes(t) < window[1]


def is_trading_session(now: Optional[datetime] = None,
                       holidays: Optional[set[str]] = None) -> bool:
    """连续竞价时段：交易日 09:30–11:30 / 13:00–15:00（UTC+8）。"""
    t = now_cn(now)
    if not is_trading_day(t.date(), holidays):
        return False
    hm = _minutes(t)
    return (AM_START <= hm < AM_END) or (PM_START <= hm < PM_END)


def in_open_exec_window(now: Optional[datetime] = None,
                        holidays: Optional[set[str]] = None) -> bool:
    """是否在开盘执行计划窗口内（09:30–10:00）。"""
    t = now_cn(now)
    return is_trading_day(t.date(), holidays) and _in_window(t, OPEN_EXEC_WINDOW)


def in_plan_window(now: Optional[datetime] = None,
                   holidays: Optional[set[str]] = None) -> bool:
    """是否在收盘后做计划窗口内（16:30–18:30）。"""
    t = now_cn(now)
    return is_trading_day(t.date(), holidays) and _in_window(t, PLAN_WINDOW)


def market_phase(now: Optional[datetime] = None,
                 holidays: Optional[set[str]] = None) -> str:
    """给面板/状态看的中文阶段名。"""
    t = now_cn(now)
    if not is_trading_day(t.date(), holidays):
        return "休市"
    hm = _minutes(t)
    if hm < AM_START:
        return "盘前"
    if hm < AM_END:
        return "上午盘"
    if hm < PM_START:
        return "午休"
    if hm < PM_END:
        return "下午盘"
    if hm < PLAN_WINDOW[0]:
        return "收盘后等待数据"
    if hm < PLAN_WINDOW[1]:
        return "收盘计划窗口"
    return "收盘后"


def windows() -> list[dict]:
    """权威运行窗口（只读展示用；顺序即日常节奏）。"""
    return [
        {"time": "09:30", "end": "10:00", "action": "open_exec",
         "label": "开盘执行计划", "note": "只消费前一交易日已存计划，不临时问模型"},
        {"time": "盘中", "end": "15:00", "action": "event",
         "label": "异动复盘", "note": "每分钟检查一次，只有价格/回撤异动才调模型"},
        {"time": "16:30", "end": "18:30", "action": "plan",
         "label": "收盘计划", "note": "数据定稿后生成下一交易日计划，成功即停"},
        {"time": "每分钟", "end": "15:00", "action": "engine",
         "label": "规则/条件执行", "note": "引擎每分钟盯价，AI 不参与逐笔撮合"},
    ]
