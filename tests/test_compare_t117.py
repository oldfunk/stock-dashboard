"""T117：默认策略对照（MA20/60 vs MA5/20，历史回测，不承诺收益）。"""
import math
from datetime import datetime, timedelta

import paper_trading.cli as hb
from paper_trading.models import Bar
from paper_trading.strategy.compare import compare_default


def _bridge(tmp_path):
    b = hb.TradingBridge(data_db=str(tmp_path / "data.db"),
                         account_db=str(tmp_path / "acct.db"),
                         config_path="/nonexistent.yaml",
                         secrets_path=str(tmp_path / "s.json"))
    bars = []
    for i in range(160):
        px = 20 + 4 * math.sin(i / 7.0) + i * 0.02
        bars.append(Bar(symbol="600519",
                        timestamp=datetime(2025, 1, 1) + timedelta(days=i),
                        open=px, high=px + 0.5, low=px - 0.5,
                        close=px, volume=100000))
    b.data_db.upsert_bars(bars)
    return b


def test_compare_runs_both_variants_same_window(tmp_path):
    b = _bridge(tmp_path)
    out = compare_default(b, days=80, symbols=["600519"])
    assert out["ok"] is True
    assert out["window"]["trading_days"] == 80
    assert out["window"]["in_sample"][0] == out["window"]["start"]
    assert [v["name"] for v in out["variants"]] == ["ma20_60", "ma5_20_legacy"]
    for v in out["variants"]:
        assert set(v["runs"]) == {"full", "in_sample", "out_of_sample"}
        assert "error" not in v["runs"]["full"]
        assert "total_return_pct" in v["runs"]["full"]
    assert len(out["comparison"]) == 2
    assert "不是收益承诺" in out["disclaimer"]


def test_compare_fails_closed_without_data(tmp_path):
    b = _bridge(tmp_path)
    with b.data_db._connect() as conn:
        conn.execute("DELETE FROM daily_bars")
    out = compare_default(b, days=80, symbols=["600519"])
    assert out["ok"] is False and "无日线" in out["error"]
