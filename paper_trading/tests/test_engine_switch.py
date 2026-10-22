"""总开关语义：engine.enabled=false 时，规则与条件单都**不许自动成交**。

真机上发现的安全缺口：enabled 只在守护入口查过一次，cron 用 `--once` 单轮
调时绕过了它——配置写着"不自动交易"，实际照常下单。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.engine.conditional import ConditionalOrders  # noqa: E402
from paper_trading.engine.runner import ConditionRunner, EngineRunner  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def _bridge(tmp_path):
    import paper_trading.cli as hb

    return hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                            account_db=str(tmp_path / "a.db"),
                            config_path="/nonexistent.yaml",
                            secrets_path=str(tmp_path / "s.json"))


def _bars(b, sym="000001", price=10.0, n=30):
    b.data_db.upsert_bars([Bar(symbol=sym,
                               timestamp=datetime.now() - timedelta(days=10000 + i),
                               open=price, high=price * 1.01, low=price * 0.99,
                               close=price, volume=100) for i in range(n)])


def test_rules_do_not_trade_when_disabled(tmp_path):
    """规则命中但 enabled=false → 不下单，理由写明。"""
    b = _bridge(tmp_path)
    _bars(b)
    cfg = {"enabled": False, "min_ticket_cny": 0,
           "rules": [{"id": "pl", "type": "price_level", "symbol": "000001",
                      "params": {"op": "below", "value": 100},
                      "action": {"side": "buy", "value": 20000}}]}
    runner = EngineRunner(b, cfg=cfg)

    class _Q(dict):
        pass

    # 直接走执行路径（绕开"非交易时段"），验证开关本身
    from paper_trading.engine.rules import Intent

    res = runner._execute(cfg["rules"][0],
                           Intent("pl", "000001", "buy", "测试", 10.0),
                           {"price": 10.0, "prev_close": 10.0, "high": 10.0,
                            "low": 10.0, "change_pct": 0.0},
                           datetime.now(), datetime.now().date().isoformat(),
                           {"orders": 0, "turnover": 0.0}, 0, 0.0, 0, 0.0)
    assert not res.get("ok")
    assert "未启用" in res.get("why", "")
    assert b.broker.get_position("000001") is None
    # dry-run 观察：被开关拦下的意图也要落库（每天同规则/标的一次）
    tr = runner.state.triggers_today(datetime.now().date().isoformat())
    assert any(t["status"] == "blocked_disabled" and t["symbol"] == "000001"
               for t in tr)
    runner._execute(cfg["rules"][0],
                    Intent("pl", "000001", "buy", "测试", 10.0),
                    {"price": 10.0, "prev_close": 10.0, "high": 10.0,
                     "low": 10.0, "change_pct": 0.0},
                    datetime.now(), datetime.now().date().isoformat(),
                    {"orders": 0, "turnover": 0.0}, 0, 0.0, 0, 0.0)
    tr2 = runner.state.triggers_today(datetime.now().date().isoformat())
    assert len([t for t in tr2 if t["status"] == "blocked_disabled"]) == 1


def test_rules_trade_when_enabled(tmp_path):
    b = _bridge(tmp_path)
    _bars(b)
    cfg = {"enabled": True, "min_ticket_cny": 0,
           "rules": [{"id": "pl", "type": "price_level", "symbol": "000001",
                      "params": {"op": "below", "value": 100},
                      "action": {"side": "buy", "value": 20000}}]}
    runner = EngineRunner(b, cfg=cfg)
    from paper_trading.engine.rules import Intent

    res = runner._execute(cfg["rules"][0],
                           Intent("pl", "000001", "buy", "测试", 10.0),
                           {"price": 10.0, "prev_close": 10.0, "high": 10.0,
                            "low": 10.0, "change_pct": 0.0},
                           datetime.now(), datetime.now().date().isoformat(),
                           {"orders": 0, "turnover": 0.0}, 0, 0.0, 0, 0.0)
    assert res.get("ok"), res
    assert b.broker.get_position("000001") is not None


def test_conditions_do_not_fill_when_disabled(tmp_path):
    from paper_trading.engine.state import EngineState

    b = _bridge(tmp_path)
    _bars(b)
    co = ConditionalOrders(str(tmp_path / "cond.db"))
    co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
               "cond_value": 10.0, "value": 20000})
    quotes = {"000001": {"code": "000001", "price": 9.9, "prev_close": 10.0,
                         "high": 10.0, "low": 9.9, "volume": 1e6, "change_pct": -1.0}}
    state = EngineState(b.account_db_path)
    r = ConditionRunner(b, co, enabled=False, min_ticket_cny=0,
                        state=state).tick(quotes)
    assert not r["filled"]
    # 仍然是待触发（没被作废），等人开了开关再说
    assert co.list_all(1)[0]["status"] == "armed"
    assert b.broker.get_position("000001") is None
    assert any(t["status"] == "blocked_disabled"
               for t in state.triggers_today(datetime.now().date().isoformat()))


def test_conditions_fill_when_enabled(tmp_path):
    b = _bridge(tmp_path)
    _bars(b)
    co = ConditionalOrders(str(tmp_path / "cond2.db"))
    co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
               "cond_value": 10.0, "value": 20000})
    quotes = {"000001": {"code": "000001", "price": 9.9, "prev_close": 10.0,
                         "high": 10.0, "low": 9.9, "volume": 1e6, "change_pct": -1.0}}
    r = ConditionRunner(b, co, enabled=True, min_ticket_cny=0).tick(quotes)
    assert r["filled"]
    assert co.list_all(1)[0]["status"] == "filled"


def test_dry_run_ignores_the_switch(tmp_path):
    """试运行只看判定结果，开关关着也要能看到"本来会买"。"""
    b = _bridge(tmp_path)
    _bars(b)
    co = ConditionalOrders(str(tmp_path / "cond3.db"))
    co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
               "cond_value": 10.0, "value": 20000})
    quotes = {"000001": {"code": "000001", "price": 9.9, "prev_close": 10.0,
                         "high": 10.0, "low": 9.9, "volume": 1e6, "change_pct": -1.0}}
    r = ConditionRunner(b, co, enabled=False, dry_run=True,
                         min_ticket_cny=0).tick(quotes)
    assert r["filled"] and r["filled"][0]["why"] == "试运行"
    assert b.broker.get_position("000001") is None
