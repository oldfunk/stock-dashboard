"""日内规则（分钟线）单测：判定纯函数 + 回测重放 + 拉取节流。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.engine.backtest import Backtester, FeeModel, load_minutes  # noqa: E402
from paper_trading.engine.rules import (INTRADAY_TYPES, RULE_TYPES,  # noqa: E402
                                        RuleContext, evaluate, vwap)


def _mins(closes, day="2026-03-02", start="09:31", vols=None, amts=None):
    """造当日分钟线 [(ts,o,h,l,c,v,a)]。"""
    hh, mm = (int(x) for x in start.split(":"))
    out, t = [], datetime.fromisoformat(f"{day}T{start}")
    for i, c in enumerate(closes):
        ts = (t + timedelta(minutes=i)).isoformat(timespec="minutes")
        out.append((ts, c, c * 1.002, c * 0.998, c,
                    (vols[i] if vols else 1000.0),
                    (amts[i] if amts else c * 1000.0)))
    return out


def _ctx(price, minutes, **kw):
    d = {"now": "2026-03-02T10:00", "day": "2026-03-02", "symbol": "000001",
         "price": price, "prev_close": price, "minutes": minutes}
    d.update(kw)
    return RuleContext(**d)


# ---------- 纯判定 ----------

def test_vwap_weighted():
    m = _mins([10, 10, 12], vols=[100, 100, 100], amts=[1000, 1000, 1200])
    assert round(vwap(m), 4) == round((10 * 1000 + 10 * 1000 + 12 * 1200) / 3200, 4)
    assert vwap([]) is None
    assert vwap([("t", 1, 1, 1, 10, 0, 0)]) is None      # 无量无额 → None


def test_vwap_dev_buy_below_sell_above():
    ms = _mins([10, 10, 10, 10], vols=[100] * 4, amts=[1000] * 4)
    r = {"id": "v", "type": "vwap_dev", "symbol": "000001",
         "params": {"pct": 1.0, "min_bars": 3}}
    assert evaluate(r, _ctx(9.0, ms)).side == "buy"
    it = evaluate(r, _ctx(11.0, ms))
    assert it and it.side == "sell" and "均价" in it.reason
    assert evaluate(r, _ctx(10.0, ms)) is None            # 偏离不够
    assert evaluate(r, _ctx(9.0, ms[:2])) is None         # 分钟数不足（开盘不稳）


def test_intraday_pullback_needs_drop():
    ms = _mins([10, 12, 11.5, 11.0])
    r = {"id": "p", "type": "intraday_pullback", "symbol": "000001",
         "params": {"drop_pct": 8}}
    assert evaluate(r, _ctx(11.5, ms)) is None            # 12.02 → 11.5 回落 4.3%
    it = evaluate(r, _ctx(10.5, ms))
    assert it and it.side == "sell" and it.all_out and "回落" in it.reason
    # 带涨幅门槛：当前未涨够则不触发
    r2 = dict(r, params={"drop_pct": 3, "gain_pct": 5})
    assert evaluate(r2, _ctx(11.0, ms, prev_close=12.0)) is None
    assert evaluate(r2, _ctx(11.0, ms, prev_close=10.0)) is not None
    # 未创新高（现价就是最高）不触发
    assert evaluate(r, _ctx(12.02, ms)) is None


def test_minute_ma_cross():
    ms = _mins([10, 10, 10, 10, 10, 11])
    r = {"id": "m", "type": "minute_ma_cross", "symbol": "000001",
         "params": {"short": 2, "long": 5}}
    it = evaluate(r, _ctx(11, ms))
    assert it and it.side == "buy" and "分钟" in it.reason
    assert evaluate(r, _ctx(11, ms[:4])) is None           # 数据不足
    ms2 = _mins([12, 12, 12, 12, 12, 11])
    it2 = evaluate(r, _ctx(11, ms2))
    assert it2 and it2.side == "sell"
    # 已在均线之上不再重复触发（不是每分钟都买）
    assert evaluate(r, _ctx(11.5, ms + _mins([12], start="09:37"))) is None


def test_intraday_types_registered_and_directional():
    for t in INTRADAY_TYPES:
        assert t in RULE_TYPES
    # 方向型：action.side 不覆盖信号方向
    ms = _mins([10] * 4, amts=[1000] * 4)
    r = {"id": "v", "type": "vwap_dev", "symbol": "000001",
         "params": {"pct": 1, "min_bars": 2},
         "action": {"side": "sell", "value": 10000}}
    assert evaluate(r, _ctx(9.0, ms)).side == "buy"
    # 无分钟数据 → 一律不触发（fail-closed）
    assert evaluate(r, _ctx(9.0, [])) is None
    assert evaluate({"id": "x", "type": "unknown", "symbol": "000001"},
                    _ctx(9.0, ms)) is None


# ---------- 回测重放 ----------

def _day_bars(days, base=10.0):
    from paper_trading.models import Bar

    return {"000001": [Bar(symbol="000001", timestamp=datetime.fromisoformat(d),
                           open=base, high=base * 1.02, low=base * 0.98, close=base,
                           volume=10000) for d in days]}


def test_intraday_backtest_executes_within_day():
    days = ["2026-03-02", "2026-03-03", "2026-03-04"]
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 10,
           "rules": [{"id": "vw", "type": "vwap_dev", "symbol": "000001",
                      "params": {"pct": 1, "min_bars": 3},
                      "action": {"value": 20000}, "once": False}]}
    # 前 3 分钟 10 元，随后一根砸到 9.5（低于均价 1% 以上）→ 当日买入
    ms = {("000001", "2026-03-02"): _mins([10, 10, 10, 9.5, 9.5, 9.5])}
    res = Backtester(cfg, fee=FeeModel()).run(_day_bars(days), days,
                                              initial_cash=100000.0,
                                              minutes_by_day=ms)
    assert res.intraday_days == 1
    buys = [t for t in res.trades if t["side"] == "buy"]
    assert buys and buys[0]["day"] == "2026-03-02" and buys[0]["ts"].endswith("09:34")
    assert buys[0]["volume"] % 100 == 0
    assert res.per_rule["vw"]["trades"] >= 1


def test_intraday_without_minutes_is_reported():
    days = ["2026-03-02", "2026-03-03"]
    cfg = {"rules": [{"id": "vw", "type": "vwap_dev", "symbol": "000001",
                      "params": {"pct": 1}, "action": {"value": 10000}}]}
    res = Backtester(cfg, fee=FeeModel()).run(_day_bars(days), days,
                                              initial_cash=100000.0)
    assert res.intraday_days == 0
    assert any("分钟线" in str(s.get("why", "")) for s in res.skipped)
    assert not res.trades


def test_intraday_t_plus_one_blocks_same_day_sell():
    days = ["2026-03-02", "2026-03-03"]
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 10,
           "rules": [
               {"id": "vw", "type": "vwap_dev", "symbol": "000001",
                "params": {"pct": 1, "min_bars": 3}, "action": {"value": 20000},
                "once": False},
               {"id": "pb", "type": "intraday_pullback", "symbol": "000001",
                "params": {"drop_pct": 8}, "once": False}]}
    # 砸到 9.5 触发买 → 冲高 12 → 回落 10.5 触发卖，但当日买入的份额被冻结
    ms = {("000001", "2026-03-02"): _mins([10, 10, 10, 9.5, 9.5, 12.0, 10.5])}
    res = Backtester(cfg, fee=FeeModel()).run(_day_bars(days), days,
                                              initial_cash=100000.0,
                                              minutes_by_day=ms)
    assert [t["side"] for t in res.trades] == ["buy"]
    assert any("T+1" in str(s.get("why", "")) for s in res.skipped)


def test_intraday_daily_cap_respected():
    days = ["2026-03-02", "2026-03-03"]
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 1,
           "rules": [{"id": "vw", "type": "vwap_dev", "symbol": "000001",
                      "params": {"pct": 1, "min_bars": 3},
                      "action": {"value": 20000}, "once": False}]}
    ms = {("000001", "2026-03-02"): _mins([10, 10, 10, 9.5, 9.0, 8.5, 8.0]),
          ("000001", "2026-03-03"): _mins([10, 10, 10, 9.5, 9.0, 8.5, 8.0],
                                           day="2026-03-03")}
    res = Backtester(cfg, fee=FeeModel()).run(_day_bars(days), days,
                                              initial_cash=100000.0,
                                              minutes_by_day=ms)
    for d in days:
        assert len([t for t in res.trades if t["day"] == d]) <= 1


def test_load_minutes_groups_by_symbol_day(tmp_path):
    import paper_trading.cli as hb

    db = str(tmp_path / "d.db")
    b = hb.TradingBridge(data_db=db, account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.data_db.upsert_minute_bars("000001", [
        {"ts": "2026-03-02T09:31", "open": 10, "high": 10, "low": 10, "close": 10,
         "volume": 100, "amount": 1000},
        {"ts": "2026-03-02T09:30", "open": 10, "high": 10, "low": 10, "close": 10,
         "volume": 100, "amount": 1000},
        {"ts": "2026-03-01T14:59", "open": 9, "high": 9, "low": 9, "close": 9,
         "volume": 100, "amount": 900}])
    got = load_minutes(b.data_db, ["000001"], ["2026-03-02"])
    assert list(got) == [("000001", "2026-03-02")]
    rows = got[("000001", "2026-03-02")]
    assert [r[0] for r in rows] == ["2026-03-02T09:30", "2026-03-02T09:31"]  # 按序


def test_runner_today_minutes_drops_future(tmp_path):
    """实盘口径：ts 晚于当前时分的分钟必须丢弃（否则就是未来函数）。"""
    from paper_trading.engine.runner import EngineRunner
    from paper_trading.models import Bar

    import paper_trading.cli as hb

    now = datetime(2026, 3, 2, 9, 33)
    b = hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                         account_db=str(tmp_path / "a.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    b.data_db.upsert_minute_bars("000001", [
        {"ts": "2026-03-02T09:31", "open": 10, "high": 10, "low": 10, "close": 10,
         "volume": 1, "amount": 10},
        {"ts": "2026-03-02T09:32", "open": 10, "high": 10, "low": 10, "close": 10,
         "volume": 1, "amount": 10},
        {"ts": "2026-03-02T09:59", "open": 99, "high": 99, "low": 99, "close": 99,
         "volume": 1, "amount": 99}])
    cfg = {"rules": [{"id": "vw", "type": "vwap_dev", "symbol": "000001",
                      "params": {"pct": 1}}]}
    r = EngineRunner(b, cfg=cfg)
    got = r._today_minutes("000001", now, "2026-03-02")
    assert [g[0] for g in got] == ["2026-03-02T09:31", "2026-03-02T09:32"]
    # 非日内规则不查分钟线（省掉无谓的库读与补拉）
    assert EngineRunner(b, cfg={"rules": [{"id": "ma", "type": "ma_cross",
                                           "symbol": "000001"}]}
                         )._minutes_for("000001", now, "2026-03-02") == []
    assert Bar is not None
