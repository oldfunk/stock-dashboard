"""T87–T90：策略注册表 / 参数校验 / 自定义加载 / 内置策略 / 配置装配。"""
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.models import Bar, SignalType  # noqa: E402
from paper_trading.strategy.base_strategy import BaseStrategy  # noqa: E402
from paper_trading.strategy.breakout_strategy import BreakoutStrategy  # noqa: E402
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy  # noqa: E402
from paper_trading.strategy.registry import (  # noqa: E402
    StrategyConfigError, build, build_all, build_from_config, load_specs,
)
from paper_trading.strategy.rsi_strategy import RSIStrategy  # noqa: E402


def _bars(closes, start=datetime(2026, 1, 1)):
    return [Bar(symbol="600519", timestamp=start + timedelta(days=i),
                open=c, high=c, low=c, close=c, volume=1000)
            for i, c in enumerate(closes)]


# ---------- 注册表：正常 / 未知类 / 坏参数 ----------
def test_build_ma_with_checked_params():
    st = build({"name": "ma", "class": "ma_cross",
                "params": {"short_window": 3, "long_window": 8}})
    assert isinstance(st, MACrossStrategy)
    assert (st.short_window, st.long_window) == (3, 8)
    assert st.name == "ma"


def test_unknown_class_fails_closed():
    with pytest.raises(StrategyConfigError, match="未知策略类"):
        build({"class": "no_such_strategy"})


@pytest.mark.parametrize("params,msg", [
    ({"short_window": 5, "typo": 1}, "不认识参数"),
    ({"short_window": "5"}, "整数"),
    ({"short_window": 1}, "不能小于"),
    ({"long_window": 999}, "不能大于"),
])
def test_bad_params_fail_closed(params, msg):
    with pytest.raises(StrategyConfigError, match=msg):
        build({"class": "ma_cross", "params": params})


def test_duplicate_names_fail_closed():
    with pytest.raises(StrategyConfigError, match="重复"):
        build_all([{"name": "x", "class": "ma_cross"},
                   {"name": "x", "class": "breakout"}])


def test_disabled_strategy_is_skipped():
    st = build_all([{"name": "a", "class": "ma_cross", "enabled": False},
                    {"name": "b", "class": "breakout"}])
    assert [s.name for s in st.strategies] == ["b"]


def test_composite_prefers_sell():
    class Buy(BaseStrategy):
        def generate_signals(self, bars):
            return {"X": __import__("paper_trading.models", fromlist=["Signal"]).Signal(
                symbol="X", direction=SignalType.BUY, volume=100, price=10.0)}

    class Sell(BaseStrategy):
        def generate_signals(self, bars):
            return {"X": __import__("paper_trading.models", fromlist=["Signal"]).Signal(
                symbol="X", direction=SignalType.SELL, volume=200, price=10.0)}

    st = build_all([{"name": "b", "class": "breakout"}])
    st.strategies = [Buy("b"), Sell("s")]
    sig = st.generate_signals({"X": []})["X"]
    assert sig.direction == SignalType.SELL and sig.volume == 200


# ---------- 配置优先级 ----------
def test_local_strategies_override_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("strategy:\n  strategies:\n"
                   "    - {name: a, class: ma_cross}\n", encoding="utf-8")
    (tmp_path / "strategy.local.yaml").write_text(
        "active: general\nstrategies:\n  - {name: b, class: breakout}\n",
        encoding="utf-8")
    specs = load_specs(cfg, root=tmp_path)
    assert [s["name"] for s in specs] == ["b"]


def test_empty_config_uses_builtin_default():
    st = build_from_config("no-such.yaml", root=".",
                           defaults={"short_window": 4, "long_window": 9})
    assert len(st.strategies) == 1
    ma = st.strategies[0]
    assert isinstance(ma, MACrossStrategy)
    assert (ma.short_window, ma.long_window) == (4, 9)


# ---------- 自定义策略加载（白名单） ----------
_MOD = '''
from paper_trading.strategy.base_strategy import BaseStrategy
from paper_trading.models import Signal, SignalType

class MyStrategy(BaseStrategy):
    def __init__(self, factor=1):
        super().__init__(name="custom")
        self.factor = factor
    def generate_signals(self, bars):
        return {"X": Signal(symbol="X", direction=SignalType.BUY,
                            volume=100 * self.factor, price=10.0)}

class NotAStrategy:
    pass
'''


def test_custom_strategy_from_whitelist(tmp_path, monkeypatch):
    (tmp_path / "mystrat.py").write_text(_MOD, encoding="utf-8")
    monkeypatch.setenv("PAPER_TRADING_STRATEGY_PATH", str(tmp_path))
    st = build({"name": "custom", "class": "mystrat.MyStrategy",
                "params": {"factor": 2}})
    assert st.factor == 2
    assert st.name == "custom"


def test_custom_not_subclass_rejected(tmp_path, monkeypatch):
    (tmp_path / "mystrat.py").write_text(_MOD, encoding="utf-8")
    monkeypatch.setenv("PAPER_TRADING_STRATEGY_PATH", str(tmp_path))
    with pytest.raises(StrategyConfigError, match="BaseStrategy"):
        build({"class": "mystrat.NotAStrategy"})


def test_custom_outside_whitelist_rejected(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    (other / "outside.py").write_text(_MOD, encoding="utf-8")
    monkeypatch.setenv("PAPER_TRADING_STRATEGY_PATH", str(tmp_path / "empty"))
    monkeypatch.syspath_prepend(str(other))
    with pytest.raises(StrategyConfigError, match="白名单"):
        build({"class": "outside.MyStrategy"})


def test_custom_import_failure_rejected(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_STRATEGY_PATH", "")
    with pytest.raises(StrategyConfigError, match="找不到"):
        build({"class": "no_such_pkg.Strategy"})


# ---------- 内置策略信号 ----------
def test_breakout_buys_on_new_high():
    closes = [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10.5]
    sig = BreakoutStrategy(high_window=10, exit_window=5).generate_signals(
        {"600519": _bars(closes)})
    assert sig["600519"].direction == SignalType.BUY


def test_rsi_buys_on_cross_above_oversold():
    # 先跌到 RSI<30，再一根上涨把 RSI 拉回 30 上方
    closes = [20] * 14 + [10] * 14 + [12]
    sig = RSIStrategy(period=14, buy_below=30, sell_above=70).generate_signals(
        {"600519": _bars(closes)})
    assert sig["600519"].direction == SignalType.BUY


# ---------- T88：Bridge 装配（坏配置停线 + 留痕） ----------
def test_bridge_offline_breakout_produces_signal(tmp_path):
    """T90 验收：本地日线 + breakout 配置，经 Bridge 装配后只靠策略出信号。"""
    from paper_trading.data.db_manager import DataDBManager

    data = DataDBManager(str(tmp_path / "data.db"))
    closes = [10.0] * 10 + [10.6]
    data.upsert_bars([Bar(symbol="600519",
                          timestamp=datetime(2026, 1, 1) + timedelta(days=i),
                          open=c, high=c, low=c, close=c, volume=1000)
                      for i, c in enumerate(closes)])
    cfg = tmp_path / "config.yaml"
    cfg.write_text("strategy:\n  strategies:\n"
                   "    - {name: bo, class: breakout, params: {high_window: 10}}\n",
                   encoding="utf-8")
    import paper_trading.cli as cli

    b = cli.TradingBridge(data_db=str(tmp_path / "data.db"),
                          account_db=str(tmp_path / "a.db"),
                          config_path=str(cfg))
    bars = {"600519": data.get_bars("600519", limit=30)}
    sig = b.strategy.generate_signals(bars)
    assert sig["600519"].direction == SignalType.BUY
    assert "突破" in sig["600519"].reason


def test_bridge_uses_strategy_config_and_logs_bad_config(tmp_path):
    import sqlite3

    import paper_trading.cli as cli

    cfg = tmp_path / "config.yaml"
    cfg.write_text("strategy:\n  strategies:\n"
                   "    - {name: bo, class: breakout, params: {high_window: 10}}\n",
                   encoding="utf-8")
    b = cli.TradingBridge(data_db=str(tmp_path / "d.db"),
                          account_db=str(tmp_path / "a.db"),
                          config_path=str(cfg))
    assert [s.name for s in b.strategy.strategies] == ["bo"]

    bad = tmp_path / "bad.yaml"
    bad.write_text("strategy:\n  strategies:\n    - {name: x, class: nope}\n",
                   encoding="utf-8")
    with pytest.raises(StrategyConfigError):
        cli.TradingBridge(data_db=str(tmp_path / "d2.db"),
                          account_db=str(tmp_path / "a2.db"),
                          config_path=str(bad))
    with sqlite3.connect(str(tmp_path / "a2.db")) as c:
        row = c.execute("SELECT action, ok, result FROM op_log"
                        " WHERE action='strategy:config'").fetchone()
    assert row and row[1] == 0 and "nope" in (row[2] or "")
