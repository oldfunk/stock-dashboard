"""事件驱动 AI 复盘：异动检测、冷却、预算（全离线）。"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent.events import (EventConfig, detect_events,  # noqa: E402
                                        run_event_review)


def _bridge(tmp_path):
    import paper_trading.cli as hb

    return hb.TradingBridge(
        data_db=str(tmp_path / "d.db"), account_db=str(tmp_path / "a.db"),
        config_path="/nonexistent.yaml", secrets_path=str(tmp_path / "s.json"))


class _FakeTrader:
    def __init__(self):
        self.calls = []

    def review_conditions(self, symbols, **kw):
        self.calls.append((list(symbols), kw))
        return {"ok": True, "parsed": 1, "created": [{"id": 1}],
                "rejected": [], "skipped": []}


def test_detect_events_price_and_drawdown():
    cfg = EventConfig(price_move_pct=3.0, drawdown_pct=5.0)
    quotes = {
        "600519": {"price": 9.6, "change_pct": -4.0},
        "000001": {"price": 18.0, "change_pct": -1.0},
    }
    pos = SimpleNamespace(symbol="000001", peak_price=20.0, avg_cost=10.0)
    events = detect_events(quotes, [pos], cfg)
    by = {e["symbol"]: e for e in events}
    assert by["600519"]["type"] == "price_move"
    assert by["000001"]["type"] == "drawdown"
    assert by["000001"]["severity"] > 9


def test_event_review_calls_only_on_event_and_honors_cooldowns(monkeypatch, tmp_path):
    from paper_trading.data import realtime as rt
    from paper_trading.engine.state import EngineState

    monkeypatch.setattr(rt, "is_trading_session", lambda now=None: True)
    b = _bridge(tmp_path)
    fake = _FakeTrader()
    cfg = EventConfig(price_move_pct=3.0, min_interval_min=15,
                      cooldown_min=30, max_calls_per_day=2)
    quotes = {"600519": {"price": 9.7, "change_pct": -3.2}}
    t0 = datetime(2026, 10, 22, 10, 0)

    r1 = run_event_review(b, ["600519"], now=t0, quotes=quotes,
                          cfg=cfg, trader=fake)
    assert r1["ok"] and r1["selected"] == ["600519"]
    assert len(fake.calls) == 1

    # 全局最小间隔：5 分钟后不再叫模型
    r2 = run_event_review(b, ["600519"], now=t0.replace(minute=5),
                          quotes=quotes, cfg=cfg, trader=fake)
    assert r2["skipped"] == "min-interval" and len(fake.calls) == 1

    # 过了全局间隔但未过单标冷却：仍跳过
    r3 = run_event_review(b, ["600519"], now=t0.replace(minute=20),
                          quotes=quotes, cfg=cfg, trader=fake)
    assert r3["skipped"] == "symbol-cooldown" and len(fake.calls) == 1

    # 35 分钟后允许第二次；预算用完后再跳过
    r4 = run_event_review(b, ["600519"], now=t0.replace(minute=35),
                          quotes=quotes, cfg=cfg, trader=fake)
    assert r4["ok"] and len(fake.calls) == 2
    r5 = run_event_review(b, ["600519"], now=t0.replace(minute=40),
                          quotes=quotes, cfg=cfg, trader=fake)
    assert r5["skipped"] in ("min-interval", "daily-budget")

    state = EngineState(b.account_db_path)
    day = t0.date().isoformat()
    assert int(state.get(f"event:budget:{day}", 0)) == 2


def test_event_review_no_event_does_not_call_llm(monkeypatch, tmp_path):
    from paper_trading.data import realtime as rt

    monkeypatch.setattr(rt, "is_trading_session", lambda now=None: True)
    b = _bridge(tmp_path)
    fake = _FakeTrader()
    r = run_event_review(
        b, ["600519"], now=datetime(2026, 10, 22, 10, 0),
        quotes={"600519": {"price": 10.0, "change_pct": 0.5}},
        cfg=EventConfig(), trader=fake)
    assert r["skipped"] == "no-events" and fake.calls == []
