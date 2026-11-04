"""M2 回测可复现指纹：同 bars_hash+params_hash ⇒ 同实验。

三类锁定：① 稳定性（同输入两次跑哈希与指标全等）；② 敏感性（一根 K 线
或一个参数变化必须换哈希）；③ 序无关（dict 键序/行序不影响哈希）。
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.engine.backtest import Backtester, FeeModel  # noqa: E402
from paper_trading.engine.provenance import (bars_fingerprint,  # noqa: E402
                                             params_fingerprint)
from paper_trading.models import Bar  # noqa: E402

DAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"]


def _bars(sym, closes):
    return [Bar(symbol=sym, timestamp=datetime.fromisoformat(d), open=c,
                high=c * 1.01, low=c * 0.99, close=c, volume=1000)
            for d, c in zip(DAYS, closes)]


CFG = {"min_ticket_cny": 0, "max_orders_per_day": 10,
       "max_turnover_per_day": 0,
       "rules": [{"id": "buy", "type": "price_level", "symbol": "A",
                  "params": {"op": "below", "value": 10.6},
                  "action": {"side": "buy", "value": 20000}, "once": False}]}


def test_bars_fingerprint_order_insensitive_and_sensitive():
    b1 = {"A": _bars("A", [10.0, 10.0, 10.5, 11.0, 11.0])}
    b2 = {"A": list(reversed(_bars("A", [10.0, 10.0, 10.5, 11.0, 11.0])))}
    f1 = bars_fingerprint(b1, DAYS)
    f2 = bars_fingerprint(b2, DAYS)
    assert f1["bars_hash"] == f2["bars_hash"], "行序不影响快照哈希"
    assert f1["days_hash"] and f1["period"]["n_days"] == 5
    # 一根 K 线变化 → 换哈希
    b3 = {"A": _bars("A", [10.0, 10.0, 10.5, 11.01, 11.0])}
    assert bars_fingerprint(b3, DAYS)["bars_hash"] != f1["bars_hash"]
    # 窗口变化（days）→ days_hash 变
    assert bars_fingerprint(b1, DAYS[:3])["days_hash"] != f1["days_hash"]


def test_params_fingerprint_key_order_insensitive_and_sensitive():
    assert params_fingerprint({"a": 1, "b": 2}) == \
        params_fingerprint({"b": 2, "a": 1})
    assert params_fingerprint({"a": 1}) != params_fingerprint({"a": 2})


def test_backtest_reproducible_same_fingerprint_same_result():
    """同输入两次回测：指纹与指标全等（可复现的定义）。"""
    bars = {"A": _bars("A", [10.0, 10.0, 10.5, 11.0, 11.0])}
    r1 = Backtester(CFG, fee=FeeModel()).run(bars, DAYS, initial_cash=100000)
    r2 = Backtester(CFG, fee=FeeModel()).run(bars, DAYS, initial_cash=100000)
    assert r1.fingerprint and r2.fingerprint
    assert r1.fingerprint == r2.fingerprint
    assert r1.final_equity == r2.final_equity
    assert r1.fingerprint["spec"] == "fp-v1"
    assert r1.fingerprint["params_hash"].startswith("sha256:")


def test_fingerprint_sensitive_to_params_and_data():
    bars = {"A": _bars("A", [10.0, 10.0, 10.5, 11.0, 11.0])}
    base = Backtester(CFG, fee=FeeModel()).run(bars, DAYS).fingerprint
    # 参数变化（规则阈值）
    cfg2 = {**CFG, "rules": [{**CFG["rules"][0], "params": {"op": "below",
                                                            "value": 10.9}}]}
    v_param = Backtester(cfg2, fee=FeeModel()).run(bars, DAYS).fingerprint
    assert v_param["params_hash"] != base["params_hash"]
    assert v_param["bars_hash"] == base["bars_hash"]      # 数据没变
    # 数据变化（一根 K 线）
    bars2 = {"A": _bars("A", [10.0, 10.0, 10.5, 11.0, 11.02])}
    v_data = Backtester(CFG, fee=FeeModel()).run(bars2, DAYS).fingerprint
    assert v_data["bars_hash"] != base["bars_hash"]
    assert v_data["params_hash"] == base["params_hash"]   # 参数没变
    # 费率变化（参数类）
    from dataclasses import replace
    v_fee = Backtester(CFG, fee=replace(FeeModel(), commission_rate=0.001)) \
        .run(bars, DAYS).fingerprint
    assert v_fee["params_hash"] != base["params_hash"]


def test_minutes_hash_present_when_intraday_replayed():
    bars = {"A": _bars("A", [10.0, 10.0, 10.5, 11.0, 11.0])}
    minutes = {("A", "2026-03-04"): [
        ("2026-03-04T10:00:00", 10.5, 10.6, 10.4, 10.5, 500, 5250.0)]}
    fp = bars_fingerprint(bars, DAYS, minutes)
    assert fp["minutes_hash"].startswith("sha256:") and fp["n_minutes"] == 1
    # 分钟数据变化 → minutes_hash 变，bars_hash 不变
    minutes2 = {("A", "2026-03-04"): [
        ("2026-03-04T10:00:00", 10.5, 10.6, 10.4, 10.6, 500, 5300.0)]}
    fp2 = bars_fingerprint(bars, DAYS, minutes2)
    assert fp2["minutes_hash"] != fp["minutes_hash"]
    assert fp2["bars_hash"] == fp["bars_hash"]
