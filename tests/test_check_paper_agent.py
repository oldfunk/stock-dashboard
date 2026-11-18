"""母侧纸盘体检的判定逻辑（纯函数：不读库、不跑 cron、不打网络）。

守的是这次停摆（2026-10-23 → 11-19）最直接的机器判据：
"计划窗口过去了，计划日期还停在昨天" / "开盘窗口过去了，计划还没执行"。
"""

from datetime import date, datetime

import pytest

from scripts.check_paper_agent import judge, next_trading_day, prev_trading_day

THU = datetime(2026, 11, 19, 12, 0)      # 2026-11-19 是周四
PREV = date(2026, 11, 18)
NEXT = date(2026, 11, 20)


def _judge(now, plans, *, trading_day=True, cron_ok=True):
    return judge(now, plans, trading_day=trading_day, prev_td=PREV,
                 next_td=NEXT, cron_ok=cron_ok)


def _plan(pid, plan_date, status="pending"):
    return {"id": pid, "plan_date": plan_date, "status": status}


def test_before_open_window_only_checks_cron():
    ok, msgs = _judge(datetime(2026, 11, 19, 9, 0), [])
    assert ok and msgs == []


def test_cron_missing_is_red_even_before_any_window():
    ok, msgs = _judge(datetime(2026, 11, 19, 9, 0), [], cron_ok=False)
    assert not ok and "cron" in msgs[0]


def test_plan_window_passed_without_next_day_plan_is_red():
    """本次停摆的形状：18:45 之后没有下一交易日的计划。"""
    ok, msgs = _judge(datetime(2026, 11, 19, 18, 50), [])
    assert not ok
    assert any("2026-11-20" in m for m in msgs)


def test_plan_window_passed_with_next_day_plan_is_green():
    ok, msgs = _judge(datetime(2026, 11, 19, 18, 50), [_plan(15, "2026-11-20")])
    assert ok
    assert any("#15" in m for m in msgs)


def test_pending_plan_after_open_window_is_red():
    ok, msgs = _judge(datetime(2026, 11, 19, 10, 30),
                      [_plan(16, "2026-11-19", "pending")])
    assert not ok
    assert any("未执行" in m for m in msgs)


def test_done_plan_after_open_window_is_green():
    ok, msgs = _judge(datetime(2026, 11, 19, 10, 30),
                      [_plan(16, "2026-11-19", "done")])
    assert ok
    assert any("done" in m for m in msgs)


def test_no_plan_at_all_after_open_window_is_red():
    """上一交易日收盘窗口应生成今天的计划；连计划都没有，说明那晚丢了。"""
    ok, msgs = _judge(datetime(2026, 11, 19, 10, 30), [])
    assert not ok
    assert any("没有任何计划" in m for m in msgs)


def test_holiday_does_not_require_plan():
    ok, msgs = _judge(datetime(2026, 11, 21, 18, 50), [], trading_day=False)
    assert ok and any("休市" in m for m in msgs)


@pytest.mark.parametrize("anchor, expect_prev", [
    (date(2026, 11, 23), date(2026, 11, 20)),   # 周一 -> 上周五
    (date(2026, 11, 20), date(2026, 11, 19)),   # 周五 -> 周四
])
def test_prev_trading_day_skips_weekend(anchor, expect_prev):
    assert prev_trading_day(anchor, set()) == expect_prev


def test_next_trading_day_skips_weekend():
    assert next_trading_day(date(2026, 11, 20), set()) == date(2026, 11, 23)
