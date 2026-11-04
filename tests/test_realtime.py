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
    """市场段表（口径唯一来源 `utils/markets.py`，T59/T60 收敛）。

    第 4 行原来断言 `920000 → sz920000`（注释写"仅 8/4 开头判北交所，与
    Stock Dashboard 一致"）——那是把 bug 当合同钉住了。2026-11-03 现网实测：
    腾讯 `sz920156` 回 1 个字段（空），`bj920156` 回 87 个字段（海昌智能 36.10），
    新浪实时与 5 分钟线同样只有 `bj` 前缀取得到 ⇒ 920 段就是北交所。
    **母库 `tc_encode` 有同一处错**，已写进转给 stock-dashboard 的请求。
    """
    assert rt.tc_symbol("600519") == "sh600519"
    assert rt.tc_symbol("000858") == "sz000858"
    assert rt.tc_symbol("430047") == "bj430047"
    assert rt.tc_symbol("920000") == "bj920000"      # 北交所 920 新段
    assert rt.tc_symbol("900901") == "sh900901"      # 沪市 B 股：9 字头但不是北交所


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
    # 非交易时段直接回空，不发请求（先清模块缓存，避免他单测预热干扰）
    rtm._cache = {"at": 0.0, "data": {}}
    monkeypatch.setattr(rtm, "is_trading_session", lambda now=None: False)
    assert rt.get_quotes(["600519"]) == {}
