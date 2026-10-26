"""第三方抓取硬超时（akshare 无 timeout 参数，stall 不能挂住 cron/持锁）。"""
import sys
import time as _time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.utils.timeout import call_with_timeout  # noqa: E402


def test_call_with_timeout_returns_value():
    assert call_with_timeout(lambda: 42, 1.0) == 42


def test_call_with_timeout_raises_and_returns_quickly():
    t0 = _time.time()
    with pytest.raises(TimeoutError):
        call_with_timeout(lambda: _time.sleep(10), 0.2)
    assert _time.time() - t0 < 3


def test_call_with_timeout_propagates_exception():
    def boom():
        raise ValueError("坏数据")

    with pytest.raises(ValueError, match="坏数据"):
        call_with_timeout(boom, 1.0)


def test_fetch_daily_hard_timeout(monkeypatch):
    """stall 的 akshare 必须抛 TimeoutError，且不重试、不换源拉长持锁。"""
    import types

    fake = types.ModuleType("akshare")
    fake.stock_zh_a_daily = lambda **kw: _time.sleep(10)
    fake.stock_zh_a_hist = lambda **kw: _time.sleep(10)
    monkeypatch.setitem(sys.modules, "akshare", fake)

    from paper_trading.data import akshare_fetcher as af
    from paper_trading.data.akshare_fetcher import AkshareFetcher
    # 若模块此前已被别的用例导入（绑定了真 akshare），也要换成 stall 假源
    monkeypatch.setattr(af.ak, "stock_zh_a_daily",
                        lambda **kw: _time.sleep(10))
    monkeypatch.setattr(af.ak, "stock_zh_a_hist",
                        lambda **kw: _time.sleep(10))

    t0 = _time.time()
    with pytest.raises(TimeoutError):
        AkshareFetcher.fetch_daily("600519", timeout_sec=0.2)
    assert _time.time() - t0 < 3
    assert af.DEFAULT_CALL_TIMEOUT_SEC >= 1
