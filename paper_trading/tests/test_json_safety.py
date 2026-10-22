"""对外 JSON 必须是**严格合法**的 JSON（不能出现 Infinity / NaN）。

真机踩过：profit_factor 在"没有亏损单"时是 float('inf')，
json.dumps 写成 `Infinity` → 浏览器 response.json() 直接抛 SyntaxError，
面板参数网格永远停在"搜索中…"（连报错都没有）。
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.utils.jsonx import safe  # noqa: E402


def test_safe_replaces_non_finite():
    out = safe({"a": float("inf"), "b": float("-inf"), "c": float("nan"),
                "d": 1.5, "e": [float("inf"), {"f": float("nan")}],
                "g": "普通字符串", "h": None, "i": 3})
    assert out["a"] is None and out["b"] is None and out["c"] is None
    assert out["d"] == 1.5
    assert out["e"][0] is None and out["e"][1]["f"] is None
    assert out["g"] == "普通字符串" and out["h"] is None and out["i"] == 3


def test_safe_output_is_strict_json():
    txt = json.dumps(safe({"x": float("inf"), "y": [float("nan")]}),
                     allow_nan=False)
    assert "Infinity" not in txt and "NaN" not in txt
    # 浏览器 JSON.parse 同样能吃
    assert json.loads(txt) == {"x": None, "y": [None]}


def test_metrics_profit_factor_never_infinite():
    """没有亏损单时 profit_factor 用 None（表示"无亏损"），不是 Infinity。"""
    from paper_trading.engine.backtest import Backtester, FeeModel, metrics
    from paper_trading.models import Bar
    from datetime import datetime, timedelta

    days = [(datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat()
            for i in range(30)]
    bars = {"A": [Bar(symbol="A", timestamp=datetime.fromisoformat(d), open=10,
                      high=10, low=10, close=10 + i, volume=1000)
                  for i, d in enumerate(days)]}
    cfg = {"min_ticket_cny": 0, "max_orders_per_day": 99,
           "max_turnover_per_day": 0,
           "rules": [{"id": "buy", "type": "price_level", "symbol": "A",
                      "params": {"op": "below", "value": 100},
                      "action": {"side": "buy", "value": 5000}, "once": False},
                     {"id": "sell", "type": "price_level", "symbol": "A",
                      "params": {"op": "above", "value": 1},
                      "action": {"side": "sell", "all": True}, "once": False}]}
    res = Backtester(cfg, fee=FeeModel()).run(bars, days, initial_cash=100000)
    m = metrics(res, len(days))
    pf = m["profit_factor"]
    assert pf is None or math.isfinite(pf), pf
    # 并且整个 metrics 都能严格序列化
    json.dumps(safe(m), allow_nan=False)


def test_dashboard_json_is_strict():
    """面板的 _json 出口必须拒绝非有限值（而不是吐 Infinity）。"""
    import paper_trading.dashboard as d

    class _Fake:
        def __init__(self):
            self.sent = None

        def _send(self, code, body, ctype):
            self.sent = (code, body)

    h = _Fake()
    d.Handler._json(h, {"ok": True, "data": {"pf": float("inf")}})
    code, body = h.sent
    assert code == 200
    assert b"Infinity" not in body
    assert json.loads(body.decode())["data"]["pf"] is None
