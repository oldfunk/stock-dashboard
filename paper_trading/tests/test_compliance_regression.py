"""合规能力回归套件（refactor-plan §5.1-6 的合同）。

迁移（rqalpha 等）前后各跑一次，同一份测试证明 L1 替换没把合规能力换丢。
每条用例独立命名、标注溯源；rqalpha 版对照位留 TODO 占位。
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.agent import AgentConfig, AgentTrader  # noqa: E402
from paper_trading.models import Bar  # noqa: E402
from paper_trading.integration import mother as _mother  # noqa: E402


def mkbar(sym, day, close):
    return Bar(symbol=sym, timestamp=datetime(2024, 1, 1) + timedelta(days=day),
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def _bridge(tmp, bars):
    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                        secrets_path=str(Path(tmp) / "s.json"))
    b.data_db.upsert_bars(bars)
    b.llm_save("custom", "https://x/v1", "m", "k")
    return b


def _fresh_bars(n=30, base=10.0):
    return [mkbar("600519", 10_000 + i, base + i * 0.1) for i in range(n)]


# T16-1 事故本体：执行价偏离基准价 >30% 必须拒单
# 溯源：docs/incident-2026-10-27.md §2-§3；AGENTS 硬规则 3.2
def test_compliance_price_scale_pollution_rejected():
    b = _bridge(tempfile.mkdtemp(), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0))
    res = t._execute_actions(
        actions=[{"action": "buy", "symbol": "600519", "volume": 100,
                  "price": 22.25, "price_source": "last_close",
                  "reason": "事故复现", "basis": ["recent_closes"]}],
        summary="", symbols=["600519"], all_bars={},
        latest_prices={"600519": 175.96}, cash=1000000.0, positions={},
        nav_total=1000000.0, dry_run=True, mode="live",
        now=datetime.now().isoformat(), from_plan=True)
    st = res["decisions"][0]["status"]
    assert "偏离基准价" in st or "fail-closed" in st
    # TODO(rqalpha): 同场景在 rqalpha 版本下验证 bar matcher 拒单


# T16-2 条件单尺度闸：price_* 超 [0.5x,2x] 必须进 parse_rejected
# 溯源：incident #29；R3.5
def test_compliance_condition_scale_gate():
    from paper_trading.engine.ai_conditions import parse_conditions_verbose

    exp = (datetime.now() + timedelta(days=2)).date().isoformat()
    raw = json.dumps({"conditions": [
        {"symbol": "000792", "side": "sell",
         "cond": {"type": "price_below", "value": 180.0},
         "volume": 100, "expiry": exp}]})
    valid, rejected = parse_conditions_verbose(
        raw, ["000792"], holdings=["000792"],
        prices={"000792": 22.24})
    assert not any(v["cond_type"] == "price_below" for v in valid)
    assert any("偏离现价" in r["why"] or "无法校验" in r["why"] for r in rejected)
    # TODO(rqalpha): 条件单在 rqalpha 上的触发价校验对照


# T16-3 H-1 fail-open：无独立基准价必须拒单
# 溯源：report-hermes-20261027 H-1
def test_compliance_no_reference_fails_closed():
    b = _bridge(tempfile.mkdtemp(), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0))
    res = t._execute_actions(
        actions=[{"action": "buy", "symbol": "600519", "volume": 100,
                  "reason": "无基准", "basis": ["recent_closes"]}],
        summary="", symbols=["600519"], all_bars={},
        latest_prices={"600519": 12.9}, cash=1000000.0, positions={},
        nav_total=1000000.0, dry_run=True, mode="live",
        now=datetime.now().isoformat(), from_plan=False)
    assert "fail-closed" in res["decisions"][0]["status"]
    # TODO(rqalpha): 无基准时的 fail-closed 对照


# T16-4 H-4 退化：prices 提供但缺该标的现价必须拒
# 溯源：report-hermes-20261027 H-4
def test_compliance_condition_no_price_fails_closed():
    from paper_trading.engine.ai_conditions import parse_conditions_verbose

    exp = (datetime.now() + timedelta(days=2)).date().isoformat()
    raw = json.dumps({"conditions": [
        {"symbol": "000792", "side": "sell",
         "cond": {"type": "price_below", "value": 20.0},
         "volume": 100, "expiry": exp}]})
    valid, rejected = parse_conditions_verbose(
        raw, ["000792"], holdings=["000792"], prices={})
    assert not any(v["cond_type"] == "price_below" for v in valid)
    assert any("无法校验尺度" in r["why"] for r in rejected)
    # TODO(rqalpha): 行情缺失时的条件单拒绝对照


# T16-5 H-7 旁路：close >10 万必须回退本地
# 溯源：report-hermes-20261027 H-7
def test_compliance_abs_price_sanity_fallback():
    def far_local(code, days):
        return [{"timestamp": "2026-09-01T00:00:00", "open": 22, "high": 23,
                 "low": 21, "close": 22.24, "volume": 100}]

    import sqlite3
    sd = Path(tempfile.mkdtemp())
    db = sd / "data" / "db" / "stock_dashboard.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    # minimal mother db with kline_daily
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE kline_daily (code TEXT, trade_date TEXT, open REAL,
                    high REAL, low REAL, close REAL, volume INTEGER, amount REAL,
                    close_adj REAL, close_qfq REAL)""")
    conn.execute("INSERT INTO kline_daily VALUES ('600519','2026-10-23',250000,250000,250000,250000,1,1,250000,250000)")
    conn.commit()
    conn.close()
    import os
    os.environ["STOCK_DASHBOARD_DIR"] = str(sd)
    _mother.reset_cache()
    try:
        out = _mother.daily_bars(["600519"], days=5, local_bars=far_local, mother=sd)
        blk = out["600519"]
        assert blk["source"] != "mother:kline_daily"
        assert blk["bars"][0]["close"] == 22.24
    finally:
        _mother.reset_cache()
        os.environ.pop("STOCK_DASHBOARD_DIR", None)
    # TODO(rqalpha): 数据源绝对量级 sanity 在新引擎的对应校验


# T16-6 H-5 consume-once：崩溃也消费计划
# 溯源：report-t11.md
def test_compliance_plan_consumed_on_crash(monkeypatch):
    b = _bridge(tempfile.mkdtemp(), _fresh_bars())
    t = AgentTrader(b, AgentConfig(max_orders_per_run=3, max_order_value=20000.0))
    day = datetime.now().date().isoformat()
    b.broker.save_plan(day, ["600519"], {
        "actions": [{"action": "buy", "symbol": "600519", "volume": 100,
                     "price": None, "price_source": "last_close",
                     "reason": "崩", "basis": ["recent_closes"]}],
        "summary": "崩溃计划"})
    pend = b.broker.get_pending_plan(day)
    bars = {"600519": b.data_db.get_bars("600519", limit=30)}

    def _boom(order):
        raise RuntimeError("boom")

    monkeypatch.setattr(b.broker, "submit_order", _boom)
    res = t._execute_plan(pend, bars, b.price_map(["600519"]))
    assert res["ok"] is False
    assert b.broker.get_pending_plan(day) is None
    # TODO(rqalpha): 计划消费语义在新引擎的对应保证
