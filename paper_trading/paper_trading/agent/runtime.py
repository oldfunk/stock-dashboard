"""代码写死的 AI 运行时（不再从 strategy.local.yaml 读队列）。

日常节奏：

- 09:30–10:00：开盘执行窗口。只消费当天已存计划；没有计划就空跑，
  不在盘中临时问模型。
- 盘中：`agent event` 每分钟检查异动；只有价格/回撤事件才调模型。
- 16:30–18:30：收盘计划窗口。数据必须已定稿（data_asof=今天）才问模型；
  没定稿就下个 tick 重试，成功后按 (plan_date) 幂等停止。
- 规则引擎/条件单：独立每分钟执行，不受 AI 窗口影响。

旧 `agent_schedule.queue` 仅保留读取兼容，生产路径不再使用。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from paper_trading.utils import market_time as mt
from paper_trading.utils.trading_calendar import (
    calendar_status, is_trading_day, next_trading_day,
)


def _today(now: Optional[datetime] = None) -> date:
    return mt.now_cn(now).date()


def plan_target(now: Optional[datetime] = None,
                holidays: Optional[set[str]] = None) -> str:
    """下一个交易日（计划执行日）。"""
    return next_trading_day(_today(now), holidays or set()).isoformat()


def due_action(now: Optional[datetime] = None, *,
               pending_plan: bool,
               force: bool = False) -> Optional[str]:
    """当前应该做的运行时动作。

    返回 `execute_plan` / `plan` / `None`。`force` 只供人工排障，仍拒绝休市日。
    数据是否定稿由 `_plan` 自己同步后再判断；这里不拿旧 asof 提前拦，
    否则行情源晚更新时会永远不触发同步重试。
    """
    t = mt.now_cn(now)
    if not is_trading_day(t.date()):
        return None
    if (force or mt.in_open_exec_window(t)) and pending_plan:
        return "execute_plan"
    if force or mt.in_plan_window(t):
        return "plan"
    return None


def snapshot(now: Optional[datetime] = None, *,
             pending_plan: Optional[dict] = None,
             data_asof: Optional[str] = None,
             holidays: Optional[set[str]] = None) -> dict:
    """面板/CLI 的只读运行状态（不含任何投资建议）。"""
    t = mt.now_cn(now)
    target = plan_target(t, holidays)
    return {
        "now": t.isoformat(timespec="seconds"),
        "date": t.date().isoformat(),
        "time": t.strftime("%H:%M"),
        "trading_day": is_trading_day(t.date(), holidays),
        "phase": mt.market_phase(t, holidays),
        "plan_target": target,
        "data_asof": data_asof,
        "pending_plan": ({
            "id": pending_plan.get("id"),
            "plan_date": pending_plan.get("plan_date"),
            "summary": (pending_plan.get("plan") or {}).get("summary", ""),
        } if pending_plan else None),
        "calendar": calendar_status(),
        "windows": mt.windows(),
        "sessions": [{"start": "09:30", "end": "11:30"},
                     {"start": "13:00", "end": "15:00"}],
    }
