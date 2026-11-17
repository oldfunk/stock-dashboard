"""T68 行为级断言（批复第 5 件）：改了 `engine.*`/`strategy.*` 的键，行为必须跟着变。

静态读点看不见的是这一类：**键接了线，但它所在的那条路径不跑了**（或反过来，
开关看着关着、实际那条路绕过了它）。M3 切换后 legacy 执行路径就是活例子，
所以这里全部用"真跑一轮 tick，看结果与账本"来断，而不是 grep 引用次数。

顺带把 `engine.enabled` 这条停机开关钉死：cron 走的是 `--once`，
如果只在守护入口查开关，"关掉引擎"就只是一句注释。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.cli import TradingBridge                    # noqa: E402
from paper_trading.engine.config import DEFAULTS               # noqa: E402
from paper_trading.engine.runner import EngineRunner, run_loop  # noqa: E402
from paper_trading.models import Bar, TradingConfig             # noqa: E402
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy  # noqa: E402

SYM = "600036"
RULE = {"id": "buy-test", "type": "price_level", "symbol": SYM,
        "params": {"op": "below", "value": 41.0},
        "action": {"side": "buy", "value": 6000.0}, "once": False}


def _cfg(**over):
    cfg = dict(DEFAULTS)
    cfg.update({"enabled": True, "rules": [RULE], "universe": "list",
                "min_ticket_cny": 0.0, "max_orders_per_day": 6,
                "max_turnover_per_day": 0.0, "per_symbol_cooldown_sec": 0,
                "executor": "legacy"})
    cfg.update(over)
    return cfg


@pytest.fixture()
def bridge(tmp_path):
    b = TradingBridge(data_db=str(tmp_path / "data.db"),
                      account_db=str(tmp_path / "acct.db"),
                      config_path=str(tmp_path / "nope.yaml"),
                      secrets_path=str(tmp_path / "s.json"),
                      config=TradingConfig(initial_cash=1_000_000.0))
    return b


@pytest.fixture()
def live_quotes(monkeypatch):
    """盘中实时价桩：交易时段 + 一律 40.0 元（低于规则阈值 41 ⇒ 买入意图）。"""
    from paper_trading.data import realtime as rt

    monkeypatch.setattr(rt, "is_trading_session", lambda *a, **k: True)
    monkeypatch.setattr(rt, "get_quotes",
                        lambda syms, force=False, **k: {
                            str(s): {"price": 40.0, "prev_close": 39.0,
                                    "change_pct": 2.5, "high": 40.5, "low": 39.5,
                                    "volume": 100000, "turnover": 4e6}
                            for s in syms})


def orders_of(b):
    with sqlite3.connect(str(b.account_db_path)) as c:
        return c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def triggers_of(b, status=None):
    with sqlite3.connect(str(b.account_db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute("SELECT * FROM engine_triggers").fetchall()]
    return [r for r in rows if status is None or r["status"] == status]


def tick(bridge, **cfg_over):
    return EngineRunner(bridge, cfg=_cfg(**cfg_over)).tick()


# ------------------------------------------------ 停机开关（cron 走 --once 这条路径）

def test_enabled_false_blocks_orders_even_on_the_once_path(bridge, live_quotes):
    """开关必须真的停机，且**仍要留下"被拦下的意图"**（排障要知道本来会买什么）。"""
    res = tick(bridge, enabled=False)
    assert res["fired"] == []
    assert any("引擎未启用" in s.get("why", "") for s in res["skipped"]), res["skipped"]
    assert orders_of(bridge) == 0
    assert triggers_of(bridge, "blocked_disabled"), "拦下的意图必须留痕，不许静默丢弃"


def test_enabled_true_and_legacy_executor_places_the_order(bridge, live_quotes):
    res = tick(bridge)
    assert len(res["fired"]) == 1, res
    assert orders_of(bridge) == 1


def test_once_path_does_not_depend_on_interval_sec(bridge, live_quotes):
    """`--once`（cron 用的那条）根本不看 `interval_sec`：它是守护循环的休眠参数。
    记这条不是挑刺，是把"这个键在生产路径上不生效"写死，免得下次有人改了以为生效。"""
    out = run_loop(bridge, interval_sec=3600, dry_run=True, max_ticks=1)
    assert len(out) == 1 and "rules" in out[0]


# ------------------------------------------------ 限额类：改一个键，结果就不同

def test_max_orders_per_day_stops_the_second_order(bridge, live_quotes):
    first = tick(bridge, max_orders_per_day=1)
    assert len(first["fired"]) == 1
    second = tick(bridge, max_orders_per_day=1)
    assert second["fired"] == []
    assert any("日内笔数上限" in s.get("why", "") for s in second["skipped"]), second

    # 同一个键放大到 2 ⇒ 第二笔就该成交（证明上一条真的是这个键挡的）
    EngineRunner(bridge, cfg=_cfg(max_orders_per_day=2)).state.set(
        f"budget:{datetime.now().date().isoformat()}", {"orders": 0, "turnover": 0.0})
    assert len(tick(bridge, max_orders_per_day=2)["fired"]) == 1


def test_daily_budget_applies_inside_the_same_round(bridge, live_quotes):
    """Q1（11-04 批复）：`max_orders_per_day` 是"**当日** N 单"，不是"每轮 N 单"。

    旧实现在循环外读一次预算快照 ⇒ 同轮两条规则都对着 `orders=0` 判定，
    预算 1 也能一轮灌进 2 单（`bump_budget` 其实每次都写了状态，只是没人再读）。
    这条用两条规则、一轮、预算=1 把它钉住：第二条必须被"日内笔数上限"挡下。
    """
    two = [RULE, dict(RULE, id="buy-test-2", symbol="600519")]
    out = tick(bridge, max_orders_per_day=1, rules=two)
    assert len(out["fired"]) == 1, out
    assert any("日内笔数上限" in s.get("why", "") for s in out["skipped"]), out["skipped"]
    assert orders_of(bridge) == 1, "落账笔数必须与 fired 一致（预算挡不住就是钱类错误）"


def test_per_symbol_cooldown_blocks_the_repeat(bridge, live_quotes):
    assert len(tick(bridge, per_symbol_cooldown_sec=0)["fired"]) == 1
    again = tick(bridge, per_symbol_cooldown_sec=900)
    assert again["fired"] == []
    assert any("冷却中" in s.get("why", "") for s in again["skipped"]), again


def test_min_ticket_cny_rejects_small_orders(bridge, live_quotes):
    small = tick(bridge, min_ticket_cny=50_000)
    assert small["fired"] == []
    assert any("低于最小下单金额" in s.get("why", "") for s in small["skipped"])
    assert len(tick(bridge, min_ticket_cny=500)["fired"]) == 1


def test_max_turnover_per_day_caps_the_amount(bridge, live_quotes):
    hit = tick(bridge, max_turnover_per_day=1000)
    assert hit["fired"] == []
    assert any("日内金额上限" in s.get("why", "") for s in hit["skipped"])
    assert len(tick(bridge, max_turnover_per_day=1_000_000)["fired"]) == 1


def test_rule_enabled_false_and_once_are_honored(bridge, live_quotes):
    off = dict(RULE)
    off["enabled"] = False
    assert tick(bridge, rules=[off])["fired"] == []
    once = dict(RULE)
    once["once"] = True
    assert len(tick(bridge, rules=[once])["fired"]) == 1
    assert tick(bridge, rules=[once])["fired"] == []      # 同规则当日只触发一次


# ------------------------------------------------ universe 两条口径

def test_universe_auto_uses_pool_capped_by_universe_max(bridge, monkeypatch):
    monkeypatch.setattr(bridge.data_db, "get_pool_symbols",
                        lambda: ["600036", "000792", "002192", "600989", "300390"])
    r = EngineRunner(bridge, cfg=_cfg(universe="auto", universe_max=2, rules=[]))
    assert r._pool_symbols() == ["600036", "000792"]
    assert EngineRunner(bridge, cfg=_cfg(universe="list"))._pool_symbols() == []
    # 规则未点名标的时，universe 直接决定订阅范围（=auto 才去读池）
    broad = dict(RULE)
    broad.pop("symbol")
    broad["symbols"] = None
    rr = EngineRunner(bridge, cfg=_cfg(universe="auto", universe_max=3,
                                       rules=[{"id": "x", "type": "price_level",
                                               "params": {"op": "below", "value": 41},
                                               "action": {"side": "buy", "value": 6000}}]))
    assert rr._rule_symbols() == ["600036", "000792", "002192"]


def test_validate_rejects_unusable_intervals(bridge):
    from paper_trading.engine.config import validate

    assert validate(_cfg(interval_sec=1))[0] is False
    assert validate(_cfg(max_orders_per_day=-1))[0] is False
    assert validate(_cfg(universe="auto"))[0] is True


# ------------------------------------------------ strategy 面（同一把尺子）

def _bars(closes):
    t0 = datetime(2026, 10, 1, 15, 0)
    return [Bar(symbol=SYM, timestamp=t0 + timedelta(days=i), close=c, open=c,
                high=c, low=c, volume=1000) for i, c in enumerate(closes)]


def _signal(closes, short, long_, buy=300, sell=700):
    got = MACrossStrategy(short_window=short, long_window=long_, buy_volume=buy,
                          sell_volume=sell).generate_signals({SYM: _bars(closes)})
    return got.get(SYM)


#: 锯齿收盘，最后一根砸到 7：短窗已经死叉、长窗还没反应过来
SAW_DROP = [10, 13] * 6 + [10, 7]


def test_strategy_windows_change_the_signal():
    """`short_window/long_window` 不是装饰品：同一串收盘，短窗出信号、长窗没有。"""
    fast = _signal(SAW_DROP, 2, 4)
    assert fast is not None and fast.direction.value == -1
    assert _signal(SAW_DROP, 5, 10) is None


def test_strategy_volumes_reach_the_signal_size():
    """`buy_volume/sell_volume` 分别进两个方向的信号（各写一遍就会有一个不生效）。"""
    up = [10] * 10 + [9, 12]          # 末根跳涨 ⇒ 金叉
    down = [10] * 10 + [11, 8]        # 末根砸跌 ⇒ 死叉
    buy = _signal(up, 2, 4, buy=300, sell=700)
    sell = _signal(down, 2, 4, buy=300, sell=700)
    assert buy is not None and buy.direction.value == 1 and buy.volume == 300
    assert sell is not None and sell.direction.value == -1 and sell.volume == 700
