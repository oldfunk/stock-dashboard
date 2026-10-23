"""条件单 + 参数网格单测（全离线，无网、无 LLM）。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.engine.ai_conditions import parse_conditions  # noqa: E402
from paper_trading.engine.conditional import (ConditionalOrders,  # noqa: E402
                                               triggered)
from paper_trading.engine.optimize import GRIDS, grid_search  # noqa: E402


def _bridge(tmp):
    import tempfile

    import paper_trading.cli as hb

    f1 = tempfile.mktemp(suffix=".db", dir=tmp)
    f2 = tempfile.mktemp(suffix=".db", dir=tmp)
    b = hb.TradingBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                         secrets_path=str(Path(tmp) / "s.json"))
    b.llm_save("custom", "https://x/v1", "m", "k")
    return b


# ---------- 条件单校验 ----------

def test_cond_validate_rejects_bad():
    now = datetime(2026, 10, 19, 10, 0)
    cases = [
        {"symbol": "60051", "side": "buy", "cond_type": "price_below",
         "cond_value": 10},                                   # 代码长度
        {"symbol": "510300", "side": "buy", "cond_type": "price_below",
         "cond_value": 10},                                   # 基金/ETF 前缀
        {"symbol": "600519", "side": "hold", "cond_type": "price_below",
         "cond_value": 10},                                   # 方向
        {"symbol": "600519", "side": "buy", "cond_type": "moon",
         "cond_value": 10},                                   # 类型
        {"symbol": "600519", "side": "buy", "cond_type": "price_below",
         "cond_value": -1},                                   # 触发值
        {"symbol": "600519", "side": "buy", "cond_type": "price_below",
         "cond_value": 10, "volume": 150},                    # 非整手
        {"symbol": "600519", "side": "buy", "cond_type": "price_below",
         "cond_value": 10},                                   # 买入缺量额
        {"symbol": "600519", "side": "buy", "cond_type": "price_below",
         "cond_value": 10, "value": 5000, "expiry": "2026-01-01"},  # 已过期
        {"symbol": "600519", "side": "buy", "cond_type": "price_below",
         "cond_value": 10, "value": 5000,
         "expiry": (now + timedelta(days=60)).date().isoformat()},    # 超期
    ]
    for c in cases:
        ok, msg, _ = ConditionalOrders.validate(c, now)
        assert not ok, f"应拒绝：{c} → {msg}"
    ok, msg, clean = ConditionalOrders.validate(
        {"symbol": "600519", "side": "buy", "cond_type": "price_below",
         "cond_value": 1300, "value": 8000,
         "expiry": (now + timedelta(days=2)).date().isoformat()}, now)
    assert ok and clean["expiry"] > now.isoformat(timespec="minutes")


def test_cond_lifecycle(tmp_path):
    db = str(tmp_path / "acct.db")
    co = ConditionalOrders(db)
    ok, msg, oid = co.create({"symbol": "600519", "side": "buy",
                              "cond_type": "price_below", "cond_value": 1300,
                              "value": 8000, "note": "回踩"})
    assert ok and oid == 1
    assert len(co.pending()) == 1
    co.mark(oid, "filled", 1298.5, {"volume": 100})
    assert co.pending() == []
    assert co.list_all(5, "filled")[0]["filled_price"] == 1298.5
    # 过期自动作废
    ok2, _, oid2 = co.create({"symbol": "000858", "side": "buy",
                              "cond_type": "price_below", "cond_value": 70,
                              "value": 5000,
                              "expiry": (datetime.now() + timedelta(days=1)
                                         ).date().isoformat()})
    future = datetime.now() + timedelta(days=3)
    assert co.expire_due(future) >= 1
    assert co.list_all(5, "expired")
    # 撤销
    ok3, _, oid3 = co.create({"symbol": "600036", "side": "buy",
                              "cond_type": "pct_below", "cond_value": 3,
                              "value": 6000})
    assert co.cancel(oid3)
    assert not co.cancel(oid3)


def test_triggered_pure():
    assert triggered({"cond_type": "price_below", "cond_value": 10}, 9.9)
    assert not triggered({"cond_type": "price_below", "cond_value": 10}, 10.1)
    assert triggered({"cond_type": "price_above", "cond_value": 10}, 10.1)
    assert triggered({"cond_type": "pct_below", "cond_value": 3}, 10, -3.5)
    assert not triggered({"cond_type": "pct_below", "cond_value": 3}, 10, -1.0)
    assert triggered({"cond_type": "pct_above", "cond_value": 2}, 10, 2.1)
    assert not triggered({"cond_type": "price_below", "cond_value": 10}, 0)
    assert not triggered({"cond_type": "unknown"}, 10)


# ---------- 条件单执行器 ----------

def test_condition_runner_triggers_and_fills(tmp_path):
    from paper_trading.engine.runner import ConditionRunner
    from paper_trading.models import Bar

    b = _bridge(str(tmp_path))
    b.data_db.upsert_bars([Bar(symbol="000001",
                               timestamp=datetime.now() - timedelta(days=10000 + i),
                               open=10.0, high=10.2, low=9.8,
                               close=10.0, volume=100) for i in range(30)])
    co = ConditionalOrders(str(tmp_path / "acct2.db"))
    co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
               "cond_value": 10.0, "value": 20000})
    quotes = {"000001": {"code": "000001", "price": 9.95, "prev_close": 10.0,
                         "high": 10.0, "low": 9.9, "volume": 1e6,
                         "change_pct": -0.5}}
    r = ConditionRunner(b, co, min_ticket_cny=1000, max_per_day=5).tick(quotes)
    assert r["filled"] and r["filled"][0]["symbol"] == "000001"
    o = co.list_all(5)[0]
    assert o["status"] == "filled"
    assert o["filled_price"] == pytest.approx(9.95, abs=0.05)   # 含滑点
    pos = b.broker.get_position("000001")
    assert pos and pos.total_volume > 0 and pos.available_volume == 0   # T+1
    assert pos.total_volume % 100 == 0 and pos.total_volume == 2000


def test_condition_runner_rejects_small_explicit_volume(tmp_path):
    """显式 volume 也不能绕过手续费地板（低价股 100 股 <5000 元）。"""
    from paper_trading.engine.runner import ConditionRunner
    from paper_trading.models import Bar

    b = _bridge(str(tmp_path))
    b.data_db.upsert_bars([Bar(symbol="000001",
                               timestamp=datetime.now() - timedelta(days=10000 + i),
                               open=10.0, high=10.2, low=9.8,
                               close=10.0, volume=100) for i in range(30)])
    co = ConditionalOrders(str(tmp_path / "acct-small.db"))
    co.create({"symbol": "000001", "side": "buy", "cond_type": "price_below",
               "cond_value": 10.0, "volume": 100,
               "expiry": (datetime.now() + timedelta(days=2)).date().isoformat()})
    quotes = {"000001": {"code": "000001", "price": 9.95, "prev_close": 10.0,
                         "high": 10.0, "low": 9.9, "volume": 1e6,
                         "change_pct": -0.5}}
    r = ConditionRunner(b, co, min_ticket_cny=5000).tick(quotes)
    assert not r["filled"] and r["rejected"]
    assert co.list_all(1)[0]["status"] == "rejected"


def test_condition_runner_skips_untriggered(tmp_path):
    from paper_trading.engine.runner import ConditionRunner
    from paper_trading.models import Bar

    b = _bridge(str(tmp_path))
    b.data_db.upsert_bars([Bar(symbol="600519",
                               timestamp=datetime.now() - timedelta(days=10000 + i),
                               open=1400.0, high=1410.0, low=1390.0,
                               close=1400.0, volume=100) for i in range(30)])
    co = ConditionalOrders(str(tmp_path / "acct3.db"))
    co.create({"symbol": "600519", "side": "buy", "cond_type": "price_below",
               "cond_value": 1300, "value": 20000})
    quotes = {"600519": {"code": "600519", "price": 1395.0, "prev_close": 1400.0,
                         "high": 1400.0, "low": 1390.0, "volume": 1e6,
                         "change_pct": -0.36}}
    r = ConditionRunner(b, co, min_ticket_cny=1000).tick(quotes)
    assert not r["filled"] and co.list_all(1)[0]["status"] == "armed"


# ---------- AI 条件单解析 ----------

def test_parse_conditions_filters_bad():
    raw = ('{"conditions":['
           '{"symbol":"600519","side":"buy","cond":{"type":"price_below",'
           '"value":1300},"value":8000,"expiry":"2026-10-22","reason":"回踩支撑"},'
           '{"symbol":"999999","side":"buy","cond":{"type":"price_below","value":10},'
           '"value":5000,"expiry":"2026-10-22"},'
           '{"symbol":"000858","side":"hold","cond":{"type":"price_below","value":70},'
           '"value":5000},'
           '{"symbol":"000858","side":"buy","cond":{"type":"moon","value":70},'
           '"value":5000},'
           '{"symbol":"000858","side":"buy","cond":{"type":"price_below","value":70},'
           '"value":999999},'
           '{"symbol":"000858","side":"sell","cond":{"type":"price_above","value":75},'
           '"volume":200,"reason":"止盈"}],"summary":"两条"}')
    out = parse_conditions(raw, ["600519", "000858"], max_conds=5,
                          max_value=20000, min_ticket=1000)
    syms = [c["symbol"] for c in out]
    assert "999999" not in syms and len(syms) == 2      # 池外/非法/超额被丢弃
    buy = [c for c in out if c["side"] == "buy"][0]
    assert buy["cond_type"] == "price_below" and buy["value"] == 8000
    assert buy["source"] == "ai" and "回踩" in buy["note"]
    assert parse_conditions("not json", ["600519"]) == []
    assert parse_conditions('{"conditions":"x"}', ["600519"]) == []


def test_parse_conditions_reports_rejection_reason():
    """低于手续费地板的买入不能被静默丢弃，必须给出可审计原因。"""
    from paper_trading.engine.ai_conditions import parse_conditions_verbose

    raw = ('{"conditions":['
           '{"symbol":"600519","side":"buy","cond":{"type":"pct_below",'
           '"value":10},"value":2000,"expiry":"2026-10-24","reason":"回撤"},'
           '{"symbol":"600519","side":"buy","cond":{"type":"price_below",'
           '"value":10},"volume":100,"expiry":"2026-10-24","reason":"一手"}]}')
    valid, rejected = parse_conditions_verbose(
        raw, ["600519"], max_value=20000, min_ticket=5000)
    assert valid == []
    assert len(rejected) == 2 and rejected[0]["symbol"] == "600519"
    assert "5000" in rejected[0]["why"] and "2000" in rejected[0]["why"]
    assert "低于最小金额" in rejected[1]["why"]


def test_condition_prompt_includes_ticket_floor():
    from paper_trading.engine.ai_conditions import build_prompt

    system, _user = build_prompt(
        ["600519"], {"600519": {"price": 12.9, "change_pct": 1.0}},
        [], "方案", "2026-10-22", min_ticket=5000, max_value=20000)
    assert "5000~20000" in system


def test_parse_conditions_rejects_expiry_over_limit():
    from paper_trading.engine.ai_conditions import parse_conditions_verbose

    exp = (datetime.now() + timedelta(days=10)).date().isoformat()
    raw = ('{"conditions":[{"symbol":"600519","side":"buy",'
           '"cond":{"type":"pct_below","value":3},"value":8000,'
           f'"expiry":"{exp}","reason":"等回撤"}}]}}')
    valid, rejected = parse_conditions_verbose(
        raw, ["600519"], max_value=20000, min_ticket=5000, max_days=3)
    assert valid == []
    assert rejected and "有效期" in rejected[0]["why"]


def test_walk_forward_splits_and_reports():
    """样本内选参数 → 样本外检验：两个区间必须不重叠，且都给出结果。"""
    from datetime import datetime as dt

    from paper_trading.engine.backtest import FeeModel
    from paper_trading.engine.optimize import walk_forward
    from paper_trading.models import Bar

    days = ([f"2026-05-{d:02d}" for d in range(1, 32)]
            + [f"2026-06-{d:02d}" for d in range(1, 29)]
            + [f"2026-07-{d:02d}" for d in range(1, 29)])
    # 先平 25 根再涨 20 根再跌 20 根再涨 20 根：制造"前半段好、后半段差"的过拟合场景
    closes = []
    for i in range(len(days)):
        if i < 25:
            closes.append(10.0)
        elif i < 45:
            closes.append(10.0 + (i - 25) * 0.4)
        elif i < 65:
            closes.append(18.0 - (i - 45) * 0.4)
        else:
            closes.append(10.0 + (i - 65) * 0.3)
    bars = {"600519": [Bar(symbol="600519", timestamp=dt.fromisoformat(d),
                           open=c, high=c * 1.01, low=c * 0.99, close=c,
                           volume=1000) for d, c in zip(days, closes)]}
    out = walk_forward({"rules": []}, bars, days, "ma_cross", ["600519"],
                        value=10000, fee=FeeModel(), initial_cash=100000,
                        split=0.6, top=3)
    assert not out.get("error"), out
    sp = out["split"]
    assert sp["in_days"] + sp["oos_days"] == len(days)
    assert sp["in_sample"][1] < sp["out_of_sample"][0]     # 严格按时间切开
    assert out["n_tested"] >= 1
    for row in out["out_of_sample"]:
        assert "oos_excess_pct" in row and "in_excess_pct" in row
    assert "过拟合" in out["note"]


def test_walk_forward_too_short():
    from paper_trading.engine.backtest import FeeModel
    from paper_trading.engine.optimize import walk_forward

    out = walk_forward({"rules": []}, {}, ["2026-01-0%d" % i for i in range(1, 6)],
                        "ma_cross", ["600519"], fee=FeeModel())
    assert "error" in out and "样本太短" in out["error"]


def test_intraday_grid_needs_minutes():
    """日内网格没有分钟数据就明说，不静默返回一个"跑过"的结果。"""
    from paper_trading.engine.backtest import FeeModel

    out = grid_search({"rules": []}, {"600519": []}, ["2026-05-04"], "vwap_dev",
                      ["600519"], fee=FeeModel())
    assert "error" in out and "分钟" in out["error"]


def test_intraday_grid_runs_with_minutes():
    from datetime import datetime as dt

    from paper_trading.engine.backtest import FeeModel
    from paper_trading.models import Bar

    days = ["2026-05-04", "2026-05-05"]
    bars = {"000001": [Bar(symbol="000001", timestamp=dt.fromisoformat(d),
                           open=10, high=10.2, low=9.8, close=10, volume=1000)
                       for d in days]}
    mins = {}
    for day in days:
        t = dt.fromisoformat(f"{day}T09:31")
        mins[("000001", day)] = [
            ((t + timedelta(minutes=i)).isoformat(timespec="minutes"), c, c * 1.002,
             c * 0.998, c, 1000.0, c * 1000.0)
            for i, c in enumerate([10, 10, 10, 9.0, 8.5, 8.0, 7.5, 7.0])]
    out = grid_search({"rules": []}, bars, days, "vwap_dev", ["000001"],
                      value=10000, fee=FeeModel(), minutes_by_day=mins, top=3)
    assert not out.get("error"), out
    assert out["intraday"] is True and out["intraday_days"] == 2
    assert out["variants"] > 0 and len(out["top"]) <= 3
    assert "样本极小" in out["note"]


# ---------- 参数网格 ----------

def test_grid_search_ranks_by_excess():
    from datetime import datetime as dt

    from paper_trading.engine.backtest import FeeModel
    from paper_trading.models import Bar

    days = ([f"2026-05-{d:02d}" for d in range(1, 32)]
            + [f"2026-06-{d:02d}" for d in range(1, 29)])
    # 先平 25 根再涨：平段够长（≥MA20 窗口）均线才会在拉升后真的金叉一次
    # （线性上涨永远不会交叉——短均线始终在长均线上方，交叉条件不成立）
    closes = [10.0 if i < 25 else 10.0 + (i - 25) * 0.4 for i in range(60)]
    bars = {"600519": [Bar(symbol="600519", timestamp=dt.fromisoformat(d),
                           open=c, high=c * 1.01, low=c * 0.99, close=c,
                           volume=1000) for d, c in zip(days, closes)]}
    out = grid_search({"rules": []}, bars, days, "ma_cross", ["600519"],
                      value=10000, fee=FeeModel(), initial_cash=100000)
    assert out["variants"] > 0 and out["benchmark_pct"] is not None
    assert out["top"] == sorted(out["top"], key=lambda x: x["excess_pct"],
                                reverse=True)
    assert all(t["trades"] >= 1 for t in out["top"])
    assert set(GRIDS) >= {"ma_cross", "rsi_zone", "trailing_stop"}
    bad = grid_search({"rules": []}, bars, days, "ma_cross", [], fee=FeeModel())
    assert "error" in bad


def test_grid_drops_zero_trade_variants():
    """0 笔成交的变体不算策略：熊市里"什么都不做"会排第一，必须剔除。

    真机上就踩到过：ma_cross 网格把「0 笔、收益 0%」的变体排第一，
    看起来"超额 +36%"，其实只是没交易。
    """
    from datetime import datetime as dt

    from paper_trading.engine.backtest import FeeModel
    from paper_trading.models import Bar

    days = ([f"2026-05-{d:02d}" for d in range(1, 32)]
            + [f"2026-06-{d:02d}" for d in range(1, 29)])
    # 先平 25 根再跌：只会死叉（无持仓可卖）→ 所有变体 0 笔成交
    closes = [20.0 if i < 25 else 20.0 - (i - 25) * 0.4 for i in range(60)]
    bars = {"600519": [Bar(symbol="600519", timestamp=dt.fromisoformat(d),
                           open=c, high=c * 1.01, low=c * 0.99, close=c,
                           volume=1000) for d, c in zip(days, closes)]}
    out = grid_search({"rules": []}, bars, days, "ma_cross", ["600519"],
                      value=10000, fee=FeeModel(), initial_cash=100000)
    assert out["dropped_no_trade"] > 0
    for t in out["top"]:
        assert t["trades"] >= 1, "0 笔成交的变体不得参与排名"
    assert "什么都不做" in out["note"]
    # 允许显式放宽（min_trades=0）时它们会回来
    out0 = grid_search({"rules": []}, bars, days, "ma_cross", ["600519"],
                       value=10000, fee=FeeModel(), initial_cash=100000,
                       min_trades=0)
    assert out0["variants"] > out["variants"]