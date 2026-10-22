"""分钟线采集单测：新浪 5 分钟 K 线 + 腾讯分时（HTTP 全 mock，无网）。"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.data import minute as mn  # noqa: E402


def test_market_prefix():
    assert mn._market_prefix("600036") == "sh"
    assert mn._market_prefix("000858") == "sz"
    assert mn._market_prefix("300750") == "sz"
    assert mn._market_prefix("830799") == "bj"
    assert mn._market_prefix("") == "sh"


def test_fetch_minute_sina_parses(monkeypatch):
    payload = json.dumps([
        {"day": "2026-10-19 09:35:00", "open": "42.66", "high": "42.88",
         "low": "42.50", "close": "42.85", "volume": "8335226"},
        {"day": "2026-10-19 09:40:00", "open": "42.85", "high": "42.94",
         "low": "42.70", "close": "42.83", "volume": "4765491"}])
    monkeypatch.setattr(mn, "_http_get", lambda *a, **k: payload)
    rows = mn.fetch_minute_sina("600036")
    assert len(rows) == 2
    r = rows[0]
    assert r["ts"] == "2026-10-19T09:35"
    assert r["close"] == pytest.approx(42.85)
    assert r["volume"] == pytest.approx(8335226)
    # 成交额用典型价 × 成交量估算（新浪不给成交额）
    typical = (42.88 + 42.50 + 42.85) / 3
    assert r["amount"] == pytest.approx(typical * 8335226, rel=1e-6)


def test_fetch_minute_sina_failures(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(mn, "_http_get", boom)
    assert mn.fetch_minute_sina("600036") == []
    monkeypatch.setattr(mn, "_http_get", lambda *a, **k: "null")
    assert mn.fetch_minute_sina("600036") == []
    monkeypatch.setattr(mn, "_http_get", lambda *a, **k: "{bad json")
    assert mn.fetch_minute_sina("600036") == []


def test_fetch_minute_tencent_diffs_cumulative(monkeypatch):
    payload = json.dumps({"data": {"sh600036": {"data": {
        "date": "20261019", "prec": "42.45",
        "data": ["0930 42.30 1662 7030260.00",
                 "0931 42.37 13004 54964183.00",
                 "0932 42.36 17143 72492509.00"]}}}})
    monkeypatch.setattr(mn, "_http_get", lambda *a, **k: payload)
    rows = mn.fetch_minute_tencent("600036")
    assert len(rows) == 3
    assert rows[0]["ts"] == "2026-10-19T09:30"
    assert rows[0]["volume"] == pytest.approx(1662 * 100)      # 手 → 股
    assert rows[0]["amount"] == pytest.approx(7030260.0)
    # 第二根是增量
    assert rows[1]["volume"] == pytest.approx((13004 - 1662) * 100)
    assert rows[1]["amount"] == pytest.approx(54964183.0 - 7030260.0)


def test_fetch_minute_tencent_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("dns")
    monkeypatch.setattr(mn, "_http_get", boom)
    assert mn.fetch_minute_tencent("600036") == []
    monkeypatch.setattr(mn, "_http_get", lambda *a, **k: json.dumps({"data": {}}))
    assert mn.fetch_minute_tencent("600036") == []


def test_fetch_minute_default_is_sina_5min(monkeypatch):
    """默认走新浪 5 分钟（多日历史），不混 1 分钟（混粒度会让均线不可比）。"""
    calls = []

    def fake(url, timeout=20, headers=None):
        calls.append(url)
        return json.dumps([
            {"day": "2026-10-19 09:35:00", "open": "42.6", "high": "42.9",
             "low": "42.5", "close": "42.8", "volume": "1000"}])

    monkeypatch.setattr(mn, "_http_get", fake)
    rows = mn.fetch_minute("600036", days=30,
                            now=datetime(2026, 10, 19, 15, 30))
    assert len(rows) == 1
    assert all("scale=5" in c for c in calls), calls
    assert not any("minute/query" in c for c in calls), calls


def test_fetch_minute_period1_uses_tencent_for_today(monkeypatch):
    def fake(url, timeout=20, headers=None):
        if "minute/query" in url:
            return json.dumps({"data": {"sh600036": {"data": {
                "date": "20261019",
                "data": ["0931 42.37 13004 54964183.00"]}}}})
        return json.dumps([
            {"day": "2026-10-19 09:35:00", "open": "42.6", "high": "42.9",
             "low": "42.5", "close": "42.8", "volume": "1000"}])

    monkeypatch.setattr(mn, "_http_get", fake)
    rows = mn.fetch_minute("600036", period="1", days=30,
                           now=datetime(2026, 10, 19, 15, 30))
    ts = [r["ts"] for r in rows]
    # 1 分钟模式：今天的部分用腾讯分时替换新浪的粗粒度行，不混粒度
    assert ts == ["2026-10-19T09:31"]
    assert ts == sorted(ts)          # 按时间升序
