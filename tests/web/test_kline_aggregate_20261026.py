"""K 线聚合/转换的缺值语义（2026-10-26，审计 D2）。

旧实现把缺失 OHLC/成交量当 0：会画出 0 元蜡烛、假跌幅、假地量。
新规则：缺失值保持 None；缺 OHLC 的日线不进入图表。
"""

from src.web.routes import _aggregate_kline, _to_klinecharts


def _row(day, close=10.0, high=None, low=None, volume=None):
    return {"trade_date": day, "open": close - 0.2, "close": close,
            "high": high, "low": low, "volume": volume,
            "amount": None, "turnover": None}


def test_aggregate_uses_present_high_low_only():
    rows = [_row("2026-10-01", 10.5), _row("2026-10-02", 11.0, 11.2, 9.8, 100)]
    out = _aggregate_kline(rows, "weekly")
    assert len(out) == 1
    assert out[0]["high"] == 11.2
    assert out[0]["low"] == 9.8
    assert out[0]["volume"] == 100


def test_aggregate_all_missing_stays_none():
    rows = [_row("2026-10-01", 10.5), _row("2026-10-02", 11.0)]
    out = _aggregate_kline(rows, "weekly")
    assert out[0]["high"] is None
    assert out[0]["low"] is None
    assert out[0]["volume"] is None


def test_to_klinecharts_skips_missing_ohlc():
    valid = _row("2026-10-01", 10.5, 10.8, 10.2, 100)
    broken = _row("2026-10-02", 11.0, 11.2, 9.8, 100)
    broken["close"] = None
    out = _to_klinecharts([valid, broken])
    assert len(out) == 1
    assert out[0]["close"] == 10.5
