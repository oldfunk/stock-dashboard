"""T114：多策略资金分配（config 校验 / 额度账 / 计划与执行两处闸门）。"""
import json
from datetime import datetime, timedelta

import pytest
import yaml

import paper_trading.cli as hb
from paper_trading.agent.loop import AgentConfig, AgentTrader
from paper_trading.models import Bar
from paper_trading.strategy.allocation import (
    AllocationBook,
    AllocationError,
    allocations_payload,
    budget,
    check_buy,
    check_names,
    load_allocations,
    validate_allocations,
)


def test_validate_allocations_happy_and_empty():
    assert validate_allocations(None) == {}
    assert validate_allocations({}) == {}
    assert validate_allocations({"ma_cross": 0.6, "rsi_zone": 0.4}) == {
        "ma_cross": 0.6, "rsi_zone": 0.4}
    # 允许留现金余量
    assert validate_allocations({"ma_cross": 0.3})["ma_cross"] == 0.3


@pytest.mark.parametrize("raw", [
    {"ma_cross": 0}, {"ma_cross": -0.1}, {"ma_cross": 1.2},
    {"ma_cross": True}, {"ma_cross": "0.5"},
    {"ma_cross": 0.7, "rsi_zone": 0.5},
    {"": 0.5}, {1: 0.5},
    [],
])
def test_validate_allocations_fail_closed(raw):
    with pytest.raises(AllocationError):
        validate_allocations(raw)


def test_check_names_rejects_unknown():
    check_names({}, ["ma_cross"])
    with pytest.raises(AllocationError):
        check_names({"nope": 0.5}, ["ma_cross"])


def test_budget_and_check_buy(tmp_path):
    book = AllocationBook(tmp_path / "acct.db")
    weights = {"ma_cross": 0.5}
    assert budget(0.5, 100000) == 50000
    ok, why = check_buy(weights, book, strategy="ma_cross",
                        order_value=20000, nav_total=100000)
    assert ok and why == ""

    # 未分配策略 / 缺策略名 → 拒
    assert not check_buy(weights, book, strategy="rsi_zone",
                         order_value=100, nav_total=100000)[0]
    assert "未标注策略" in check_buy(weights, book, strategy="",
                                   order_value=100, nav_total=100000)[1]

    # 同轮多笔合计超限 → 拒
    ok, why = check_buy(weights, book, strategy="ma_cross",
                        order_value=40000, nav_total=100000,
                        run_spent={"ma_cross": 20000})
    assert not ok and "额度" in why

    # 落库后跨轮累计
    book.record_buy("ma_cross", "600519", 45000)
    assert book.used("ma_cross") == 45000
    ok, why = check_buy(weights, book, strategy="ma_cross",
                        order_value=10000, nav_total=100000)
    assert not ok and "45000" in why


def test_book_release_on_sell(tmp_path):
    book = AllocationBook(tmp_path / "acct.db")
    book.record_buy("ma_cross", "600519", 30000)
    assert book.record_sell("600519", 10000) == "ma_cross"
    assert book.used("ma_cross") == 20000
    # 超卖回冲不为负
    book.record_sell("600519", 999999)
    assert book.used("ma_cross") == 0
    assert book.record_sell("000001", 1000) is None


def test_allocations_payload_readonly(tmp_path):
    book = AllocationBook(tmp_path / "acct.db")
    book.record_buy("ma_cross", "600519", 12000)
    out = allocations_payload({"ma_cross": 0.5}, book, 100000)
    assert out["enabled"] is True
    assert out["total_weight"] == 0.5
    item = out["items"][0]
    assert item["budget"] == 50000 and item["used"] == 12000
    assert item["remaining"] == 38000
    assert allocations_payload({}, book, 100000)["enabled"] is False


def _mkcfg(tmp_path, allocations) -> str:
    cfg = {
        "stock_pool": ["600519"],
        "strategy": {"active": "general", "short_window": 20,
                     "long_window": 60, "allocations": allocations},
        "risk": {"max_single_order_value": 30000},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return str(p)


def test_bridge_wires_allocations(tmp_path):
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path=_mkcfg(tmp_path, {"ma_cross": 0.5}),
                         secrets_path=str(tmp_path / "s.json"))
    assert b.allocations == {"ma_cross": 0.5}
    ctx = b.llm_context(max_ops=1, max_nav=1, closes_n=1)
    assert ctx["strategy_allocations"]["enabled"] is True
    assert ctx["strategy_allocations"]["items"][0]["strategy"] == "ma_cross"
    assert "strategy_allocations" in ctx["legend"]


def test_bridge_rejects_unknown_strategy(tmp_path):
    with pytest.raises(AllocationError):
        hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path=_mkcfg(tmp_path, {"nope": 0.5}),
                         secrets_path=str(tmp_path / "s.json"))


def _bars(n=30, base=10.0):
    return [Bar(symbol="600519",
                timestamp=datetime(2024, 1, 1) + timedelta(days=i),
                open=base + i * 0.1, high=base + i * 0.1 + 1,
                low=base + i * 0.1 - 1, close=base + i * 0.1, volume=100)
            for i in range(n)]


def _trader(tmp_path, allocations):
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path=_mkcfg(tmp_path, allocations),
                         secrets_path=str(tmp_path / "s.json"))
    b.data_db.upsert_bars(_bars())
    return b, AgentTrader(b, AgentConfig(max_orders_per_run=3,
                                         max_order_value=20000.0))


def test_plan_filter_caps_buys_by_envelope(tmp_path):
    b, t = _trader(tmp_path, {"ma_cross": 0.5})
    nav = 100000.0
    keep, rejected = t._filter_alloc_actions([
        {"action": "sell", "symbol": "600519", "volume": 100, "strategy": "ma_cross"},
        {"action": "buy", "symbol": "600519", "volume": 3000,
         "price": 10.0, "strategy": "ma_cross"},          # 30000 ≤ 50000
        {"action": "buy", "symbol": "600519", "volume": 3000,
         "price": 10.0, "strategy": "ma_cross"},          # 合计 60000 > 50000
        {"action": "buy", "symbol": "600519", "volume": 100,
         "price": 10.0},                                   # 缺 strategy
    ], nav)
    assert len(keep) == 2 and len(rejected) == 2
    assert "额度" in rejected[0]["reject_reason"]
    assert "未标注策略" in rejected[1]["reject_reason"]


def test_plan_filter_noop_without_config(tmp_path):
    b, t = _trader(tmp_path, {})
    acts = [{"action": "buy", "symbol": "600519", "volume": 100, "price": 10.0}]
    keep, rejected = t._filter_alloc_actions(acts, 100000.0)
    assert keep == acts and rejected == []


def test_fill_records_usage_once(tmp_path):
    """成交后 book 只记一次；不能既记 book 又记 run_spent（会双计）。"""
    b, t = _trader(tmp_path, {"ma_cross": 0.5})
    res = t._execute_actions(
        actions=[{"action": "buy", "symbol": "600519", "volume": 100,
                  "reason": "买", "basis": ["recent_closes"], "risk_stop": 8.0,
                  "price": 12.9, "price_source": "last_close",
                  "strategy": "ma_cross"}],
        summary="", symbols=["600519"], all_bars={"600519": _bars()},
        latest_prices={"600519": 12.9}, cash=1000000.0, positions={},
        nav_total=100000.0, dry_run=False, mode="live",
        now=datetime.now().isoformat(), from_plan=True, plan_id=1)
    st = res["decisions"][0]["status"]
    assert st.startswith("已成交"), st
    used = b.alloc_book.used("ma_cross")
    assert 1200 < used < 1400, f"used 应约为一笔成交额，实际 {used}"


def test_execute_rejects_over_envelope_but_allows_sell(tmp_path):
    second = tmp_path / "second"
    second.mkdir()
    b, t = _trader(second, {"ma_cross": 0.5})
    b.alloc_book.record_buy("ma_cross", "600036", 49000)   # 已用近满
    res = t._execute_actions(
        actions=[
            {"action": "buy", "symbol": "600519", "volume": 100,
             "reason": "买", "basis": ["recent_closes"], "risk_stop": 8.0,
             "price": 12.9, "price_source": "last_close",
             "strategy": "ma_cross"},
            {"action": "sell", "symbol": "600036", "volume": 100,
             "reason": "卖", "basis": ["recent_closes"],
             "price": 10.0, "price_source": "last_close",
             "strategy": "ma_cross"},
        ],
        summary="", symbols=["600519"], all_bars={"600519": _bars()},
        latest_prices={"600519": 12.9, "600036": 10.0},
        cash=1000000.0,
        positions={"600036": type("P", (), {"symbol": "600036", "volume": 100,
                                            "available_volume": 100,
                                            "avg_cost": 9.0})(),
                   "600519": type("P", (), {"symbol": "600519", "volume": 0,
                                            "available_volume": 0,
                                            "avg_cost": 0.0})()},
        nav_total=100000.0, dry_run=True, mode="live",
        now=datetime.now().isoformat(), from_plan=True, plan_id=1)
    by_action = {d["action"]: d for d in res["decisions"]}
    assert "资金分配" in by_action["buy"]["status"]
    # 卖出不因额度被拒（dry-run 通过）
    assert by_action["sell"]["status"].startswith("试运行通过")
    assert json.dumps(res["decisions"], ensure_ascii=False)
