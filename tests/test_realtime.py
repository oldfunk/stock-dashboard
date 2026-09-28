"""腾讯实时行情单测（canned 行串 + fake HTTP，离线可跑）。"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.data import realtime as rt  # noqa: E402

LINE = ('v_sz000858="1~五粮液~000858~131.20~130.00~131.50~123456~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~'
        '1.20~0.92~132.10~129.80~0~0~500000~2.10~25.0~0~0~0~0~3000~20.0~0~0~0~0~0~0~0~0~0";')


def test_parse_line_fields():
    q = rt.parse_line(LINE)
    assert q["code"] == "000858" and q["name"] == "五粮液"
    assert q["price"] == 131.20 and q["prev_close"] == 130.00
    assert q["high"] == 132.10 and q["low"] == 129.80
    assert q["change_pct"] == 0.92 and q["turnover"] == 2.10
    assert rt.parse_line("v_sz000858=\"1~2\"") is None  # 过短丢弃
    assert rt.parse_line("garbage") is None


def test_tc_symbol_mapping():
    assert rt.tc_symbol("600519") == "sh600519"
    assert rt.tc_symbol("000858") == "sz000858"
    assert rt.tc_symbol("430047") == "bj430047"
    assert rt.tc_symbol("920000") == "sz920000"  # 仅8/4开头判北交所（与 Stock Dashboard 一致）


def test_trading_session_window():
    cn = rt.CN
    assert rt.is_trading_session(datetime(2026, 9, 28, 10, 0, tzinfo=cn))
    assert rt.is_trading_session(datetime(2026, 9, 28, 14, 0, tzinfo=cn))
    assert not rt.is_trading_session(datetime(2026, 9, 28, 8, 0, tzinfo=cn))
    assert not rt.is_trading_session(datetime(2026, 9, 28, 16, 0, tzinfo=cn))
    assert not rt.is_trading_session(datetime(2026, 9, 26, 10, 0, tzinfo=cn))  # 周六
    assert not rt.is_trading_session(datetime(2026, 9, 27, 10, 0, tzinfo=cn))  # 周日


def test_fetch_batch_empty_and_off_hours(monkeypatch):
    import paper_trading.data.realtime as rtm

    assert rt.fetch_batch([]) == {}
    # 非交易时段直接回空，不发请求
    monkeypatch.setattr(rtm, "is_trading_session", lambda now=None: False)
    assert rt.get_quotes(["600519"]) == {}
