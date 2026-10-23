"""运行时窗口、交易日历缓存、计划幂等与开盘执行（全离线）。"""
import json
import sys
import tempfile
import types
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent import AgentConfig, AgentTrader  # noqa: E402
from paper_trading.models import Bar  # noqa: E402
from paper_trading.utils import market_time as mt  # noqa: E402


def _bridge(tmp, bars=()):
    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2,
                         config_path="/nonexistent.yaml",
                         secrets_path=str(Path(tmp) / "secrets.json"))
    if bars:
        b.data_db.upsert_bars(list(bars))
    b.llm_save("custom", "https://x/v1", "m", "k")
    return b


def _bars(day: datetime, symbol="600519", n=30):
    return [Bar(symbol=symbol,
                timestamp=day.replace(hour=0, minute=0, second=0, microsecond=0)
                - __import__("datetime").timedelta(days=n - 1 - i),
                open=10.0, high=11.0, low=9.0, close=10.0, volume=100)
            for i in range(n)]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    from paper_trading.cli import TradingBridge
    from paper_trading.llm import provider as prov

    monkeypatch.setattr(
        TradingBridge, "sync_data",
        lambda self, symbols: {"ok": True, "symbols": symbols,
                               "updated": {s: 0 for s in symbols}})
    monkeypatch.setattr(prov, "chat",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("不应调用模型")))


def test_market_time_sessions_and_windows():
    d = datetime(2026, 9, 29)  # 周二
    assert mt.is_trading_session(d.replace(hour=9, minute=30))
    assert mt.is_trading_session(d.replace(hour=11, minute=29))
    assert not mt.is_trading_session(d.replace(hour=11, minute=30))
    assert not mt.is_trading_session(d.replace(hour=12, minute=0))
    assert mt.is_trading_session(d.replace(hour=13, minute=0))
    assert mt.is_trading_session(d.replace(hour=14, minute=59))
    assert not mt.is_trading_session(d.replace(hour=15, minute=0))
    assert not mt.is_trading_session(datetime(2026, 9, 26, 10, 0))
    assert mt.in_open_exec_window(d.replace(hour=9, minute=30))
    assert not mt.in_open_exec_window(d.replace(hour=10, minute=0))
    assert mt.in_plan_window(d.replace(hour=16, minute=30))
    assert not mt.in_plan_window(d.replace(hour=18, minute=30))
    assert mt.market_phase(d.replace(hour=12, minute=0)) == "午休"


def test_calendar_cache_controls_holidays(tmp_path, monkeypatch):
    from paper_trading.utils.trading_calendar import (
        CALENDAR_ENV, calendar_status, clear_calendar_cache, is_trading_day,
        next_trading_day, save_calendar,
    )

    p = tmp_path / "calendar.json"
    save_calendar(["2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"],
                  source="test", path=p)
    monkeypatch.setenv(CALENDAR_ENV, str(p))
    clear_calendar_cache()
    assert not is_trading_day("2026-10-01")       # 缓存覆盖内的工作日节假日
    assert is_trading_day("2026-10-08")
    assert next_trading_day("2026-09-30").isoformat() == "2026-10-08"
    st = calendar_status()
    assert st["available"] and st["source"] == "test"
    assert st["min_date"] == "2026-09-29" and st["max_date"] == "2026-10-09"
    clear_calendar_cache()


def test_sync_keeps_future_drops_history(tmp_path, monkeypatch):
    """日历同步是未来优先：全量历史不入库，只留最近尾部 + 全部未来。"""
    from paper_trading.utils import trading_calendar as tc

    today = date.today()
    recent = (today - timedelta(days=40)).isoformat()   # 落在 45 天尾部内
    old = (today - timedelta(days=120)).isoformat()      # 超出尾部的历史
    future = (today + timedelta(days=300)).isoformat()   # 远期未来

    class _Col:
        def __init__(self, vals):
            self._vals = vals

        def tolist(self):
            return self._vals

    class _DF:
        columns = ["trade_date"]

        def __init__(self, vals):
            self._vals = vals

        def __getitem__(self, _key):
            return _Col(self._vals)

    fake = types.ModuleType("akshare")
    fake.tool_trade_date_hist_sina = lambda: _DF(
        ["1990-12-19", old, recent, future])
    monkeypatch.setitem(sys.modules, "akshare", fake)

    p = tmp_path / "cal.json"
    res = tc.sync_calendar(path=p)
    assert res["ok"] and res["history_tail_days"] == tc.HISTORY_TAIL_DAYS
    rows = set(json.loads(p.read_text(encoding="utf-8"))["dates"])
    assert "1990-12-19" not in rows and old not in rows  # 历史被裁掉
    assert recent in rows and future in rows             # 尾部+未来保留

    monkeypatch.setenv(tc.CALENDAR_ENV, str(p))
    tc.clear_calendar_cache()
    st = tc.calendar_status()
    assert st["available"] and st["max_date"] == future
    assert st["future_days"] >= 1
    assert st["lookahead_days"] == (date.fromisoformat(future) - today).days
    assert st["covers_today"] and not st["needs_sync"]
    tc.clear_calendar_cache()


def test_calendar_cache_drives_realtime_session(tmp_path, monkeypatch):
    from paper_trading.data import realtime as rt
    from paper_trading.utils.trading_calendar import (
        CALENDAR_ENV, clear_calendar_cache, save_calendar,
    )

    p = tmp_path / "calendar.json"
    save_calendar(["2026-09-30", "2026-10-08", "2026-10-09"],
                  source="test", path=p)
    monkeypatch.setenv(CALENDAR_ENV, str(p))
    clear_calendar_cache()
    assert not rt.is_trading_session(datetime(2026, 10, 1, 10, 0))
    assert rt.is_trading_session(datetime(2026, 10, 8, 10, 0))
    clear_calendar_cache()


def test_tick_outside_window_is_silent(tmp_path):
    b = _bridge(str(tmp_path), _bars(datetime(2026, 10, 22)))
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    before = len(b.broker.get_op_log_day("2026-10-22"))
    r = t.tick(["600519"], now=datetime(2026, 10, 22, 12, 0))
    assert r["skipped"] == "outside-runtime-window"
    assert len(b.broker.get_op_log_day("2026-10-22")) == before


def test_tick_open_without_plan_does_not_call_llm(tmp_path):
    b = _bridge(str(tmp_path), _bars(datetime(2026, 10, 22)))
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    r = t.tick(["600519"], now=datetime(2026, 10, 22, 9, 30))
    assert r["skipped"] == "no-pending-plan"
    assert not [o for o in b.broker.get_op_log_day("2026-10-22")
                if o["action"].startswith("ai:")]


def test_tick_open_executes_today_plan(tmp_path, monkeypatch):
    today = datetime.now().date()
    if not mt.is_trading_day(today):
        pytest.skip("今天不是交易日")
    day = datetime.combine(today, time(9, 30))
    b = _bridge(str(tmp_path), _bars(day))
    plan = {"actions": [{"action": "buy", "symbol": "600519",
                         "volume": 100, "price": 10.0, "reason": "计划买"}],
            "summary": "开盘计划"}
    b.broker.save_plan(today.isoformat(), ["600519"], plan)
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    r = t.tick(["600519"], now=day)
    assert r.get("runtime_action") == "open_exec"
    assert b.broker.get_position("600519") is not None
    assert b.broker.get_pending_plan(today.isoformat()) is None


def test_tick_plan_window_is_idempotent(tmp_path, monkeypatch):
    seen = {"n": 0}

    def fake(cfg, messages, system=""):
        seen["n"] += 1
        return json.dumps({"actions": [], "summary": "计划"})

    from paper_trading.llm import provider as prov
    monkeypatch.setattr(prov, "chat", fake)

    b = _bridge(str(tmp_path), _bars(datetime(2026, 10, 22)))
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    r1 = t.tick(["600519"], now=datetime(2026, 10, 22, 16, 30))
    assert r1["ok"] and r1["runtime_action"] == "plan"
    assert r1["plan_date"] == "2026-10-23"
    assert b.broker.get_pending_plan("2026-10-23") is not None
    r2 = t.tick(["600519"], now=datetime(2026, 10, 22, 16, 35))
    assert r2["skipped"] == "plan-exists"
    assert seen["n"] == 1


def test_tick_plan_waits_for_today_data(tmp_path):
    b = _bridge(str(tmp_path), _bars(datetime(2026, 10, 21)))
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    r = t.tick(["600519"], now=datetime(2026, 10, 22, 16, 30))
    assert r["skipped"] == "no-fresh-data"
    assert r["asof"] == "2026-10-21"
    assert b.broker.get_pending_plan("2026-10-23") is None


def test_tick_rejects_holiday_even_with_force(tmp_path, monkeypatch):
    from paper_trading.utils.trading_calendar import (
        CALENDAR_ENV, clear_calendar_cache, save_calendar,
    )

    p = tmp_path / "calendar.json"
    save_calendar(["2026-09-30", "2026-10-08"], source="test", path=p)
    monkeypatch.setenv(CALENDAR_ENV, str(p))
    clear_calendar_cache()
    b = _bridge(str(tmp_path), _bars(datetime(2026, 10, 1)))
    t = AgentTrader(b, AgentConfig(picks_enabled=False))
    r = t.tick(["600519"], now=datetime(2026, 10, 1, 9, 30), force=True)
    assert r["skipped"] == "not-trading-day"
    clear_calendar_cache()
