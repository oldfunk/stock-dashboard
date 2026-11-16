"""N10（批7）：每日流水线的重试预算与"跳过 ≠ 完成"语义（2026-10-29）。

旧实现两个方向都错：
- 异常分支不记 `_last_daily_date` → 调度循环每 30 秒重跑一次全市场采集直到午夜；
- `run_daily_pipeline` 因锁竞争返回 False 时返回值被忽略 → "跳过"被当成
  "今日已完成"，当天收盘选股永久丢失。
现在失败/跳过都消耗每日预算（默认 3 次），只有返回 True 才算完成。
"""

import logging
from datetime import date, datetime, time, timedelta

import pytest

from src.models import database as db_mod


def _weekday_at(hh=15, mm=35, start=date(2026, 10, 26)):
    """推导一个工作日 15:35（不写死星期，避免日历算错造成假绿）。"""
    d = start
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, time(hh, mm))


@pytest.fixture
def sched(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "s.db"))
    db_mod.init_database()
    import src.scheduler as s
    m = s.MarketScheduler()
    monkeypatch.setattr(s, "now_cn", lambda: _weekday_at())
    monkeypatch.setattr(m, "_fetch_kline_daily", lambda: None)
    # 默认不让用例真的拉起 AI 分析线程（用例 2026-11-16 恢复自动触发，
    # 见下方两个专用用例；其余用例不涉及该路径）
    monkeypatch.setattr(m, "_trigger_ai_analysis_async", lambda config: None)
    m._last_daily_date = None
    return s, m


def test_failure_no_longer_re_floods_every_30s(sched, monkeypatch):
    """异常最多重试到预算上限，不再每 30 秒全市场重跑。"""
    s, m = sched
    import src.orchestrator as o
    calls = []

    def boom(config=None):
        calls.append(1)
        raise RuntimeError("akshare 炸了")

    monkeypatch.setattr(o, "run_daily_pipeline", boom)
    for _ in range(10):                     # 模拟 10 轮 30 秒心跳
        m._check_daily_pipeline()
    assert len(calls) == m.daily_max_attempts == 3, \
        f"应止步于预算次数，实际跑了 {len(calls)} 次"


def test_contention_skip_is_not_marked_done(sched, monkeypatch):
    """返回 False 是"跳过"：不得置完成，且预算内还会再试。"""
    s, m = sched
    import src.orchestrator as o
    monkeypatch.setattr(o, "run_daily_pipeline", lambda config=None: False)
    m._check_daily_pipeline()
    assert m._last_daily_date is None, "跳过不得置完成，否则今日选股永久丢失"
    assert m._daily_attempts == 1

    monkeypatch.setattr(o, "run_daily_pipeline", lambda config=None: True)
    m._check_daily_pipeline()
    assert m._last_daily_date == _weekday_at().date(), "真正跑完才算完成"


def test_success_stops_further_attempts(sched, monkeypatch):
    s, m = sched
    import src.orchestrator as o
    calls = []
    monkeypatch.setattr(o, "run_daily_pipeline",
                        lambda config=None: calls.append(1) or True)
    for _ in range(5):
        m._check_daily_pipeline()
    assert len(calls) == 1, "成功后当日不应再触发"


def test_budget_resets_on_new_day(sched, monkeypatch):
    """新交易日预算必须归零（昨天的失败不能吃掉今天的机会）。"""
    s, m = sched
    import src.orchestrator as o
    m._daily_attempt_date = _weekday_at().date() - timedelta(days=1)
    m._daily_attempts = 3
    calls = []
    monkeypatch.setattr(o, "run_daily_pipeline",
                        lambda config=None: calls.append(1) or True)
    m._check_daily_pipeline()
    assert len(calls) == 1 and m._daily_attempts == 1


def test_give_up_logs_once_not_every_beat(sched, monkeypatch, caplog):
    """预算耗尽只告警一次，别每 30 秒刷 error 把日志淹掉。"""
    s, m = sched
    import src.orchestrator as o
    monkeypatch.setattr(
        o, "run_daily_pipeline",
        lambda config=None: (_ for _ in ()).throw(RuntimeError("x")))
    with caplog.at_level(logging.ERROR, logger="src.scheduler"):
        for _ in range(6):
            m._check_daily_pipeline()
    gave_up = [r for r in caplog.records if "不再重试" in r.getMessage()]
    assert len(gave_up) == 1, f"放弃告警应恰好一次，实际 {len(gave_up)}"


def test_weekend_and_premarket_never_consume_budget(sched, monkeypatch):
    """非交易日 / 未到 15:30 不消耗预算（否则周末会把当天的额度白烧掉）。"""
    s, m = sched
    import src.orchestrator as o
    calls = []
    monkeypatch.setattr(o, "run_daily_pipeline",
                        lambda config=None: calls.append(1) or True)
    monkeypatch.setattr(s, "now_cn",
                        lambda: _weekday_at(hh=9, mm=0))       # 未到点
    m._check_daily_pipeline()
    saturday = _weekday_at().date() + timedelta(days=1)
    if saturday.weekday() == 5:
        monkeypatch.setattr(s, "now_cn",
                            lambda: datetime.combine(saturday, time(16, 0)))
        m._check_daily_pipeline()
    assert calls == [] and m._daily_attempts == 0


def test_ai_analysis_triggered_after_pipeline(sched, monkeypatch):
    """流水线成功后自动触发 AI 分析（2026-11-16 恢复；ai.enabled 默认 true）。"""
    s, m = sched
    import src.orchestrator as o
    from src.research import market_state as ms
    monkeypatch.setattr(o, "run_daily_pipeline", lambda config=None: True)
    monkeypatch.setattr(ms, "snapshot_day", lambda d: None)
    calls = []
    monkeypatch.setattr(m, "_trigger_ai_analysis_async",
                        lambda config: calls.append(1))
    m._check_daily_pipeline()
    assert calls == [1], "自带 Key 已配置，流水线完成后应自动分析当日候选"


def test_ai_analysis_disabled_by_config(sched, monkeypatch):
    """ai.enabled=false 时只保留面板手动队列，不自动触发。"""
    s, m = sched
    import src.config as cmod
    import src.orchestrator as o
    from src.research import market_state as ms
    monkeypatch.setattr(o, "run_daily_pipeline", lambda config=None: True)
    monkeypatch.setattr(ms, "snapshot_day", lambda d: None)
    monkeypatch.setattr(cmod, "load_config", lambda: {"ai": {"enabled": False}})
    calls = []
    monkeypatch.setattr(m, "_trigger_ai_analysis_async",
                        lambda config: calls.append(1))
    m._check_daily_pipeline()
    assert calls == []


def test_kline_rotation_stale_first_and_limited(sched, monkeypatch):
    """全市场轮换：最陈旧优先、限量；锚点日（最新）的票不参与。"""
    s, m = sched
    import src.collector.akshare_fetcher as af
    from src.models.database import KlineDAO

    dao = KlineDAO()
    for code, d in (("000001", "2026-10-01"), ("000002", "2026-10-20"),
                    ("000003", "2026-11-10")):
        dao.upsert_many(code, [{
            "trade_date": d, "open": 1.0, "close": 1.0, "high": 1.0,
            "low": 1.0, "volume": 1.0, "amount": 1.0, "turnover": 0.1,
            "close_adj": 1.0, "close_raw": 1.0}])
    calls = []

    def fake_fetch(code, start_date=None):
        calls.append(code)
        return [{"trade_date": "2026-11-13", "open": 1.0, "close": 1.0,
                 "high": 1.0, "low": 1.0, "volume": 1.0, "amount": 1.0,
                 "turnover": 0.1, "close_adj": 1.0, "close_raw": 1.0}]

    m._rotate_stale_kline(dao, fake_fetch, af.SourceUnsupportedError,
                          limit=2, budget=100)
    assert calls == ["000001", "000002"], "最陈旧优先；000003 是锚点日不参与"


def test_kline_rotation_budget_stops(sched, monkeypatch):
    """时间预算到点即停（剩下明天接着最陈旧轮转）。"""
    s, m = sched
    import src.collector.akshare_fetcher as af
    from src.models.database import KlineDAO

    dao = KlineDAO()
    dao.upsert_many("000001", [{
        "trade_date": "2026-10-01", "open": 1.0, "close": 1.0, "high": 1.0,
        "low": 1.0, "volume": 1.0, "amount": 1.0, "turnover": 0.1,
        "close_adj": 1.0, "close_raw": 1.0}])
    dao.upsert_many("000002", [{
        "trade_date": "2026-11-10", "open": 1.0, "close": 1.0, "high": 1.0,
        "low": 1.0, "volume": 1.0, "amount": 1.0, "turnover": 0.1,
        "close_adj": 1.0, "close_raw": 1.0}])
    calls = []
    m._rotate_stale_kline(dao, lambda code, start_date=None: calls.append(code),
                          af.SourceUnsupportedError, limit=600, budget=0)
    assert calls == [], "预算 0 应一只都不跑"
