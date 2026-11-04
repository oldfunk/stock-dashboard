"""T50 盘后防线测试（裁决 1 的 (a)+(c)，顺序即因果）。

(a) 盘后收盘快照可用 ⇒ 当日跨源对照真做得成；
(c) 没做成对照的当日 bar 进不了引擎 ⇒ 两者合起来才是"当日价有独立参照"。
全程离线：`fetch_batch`/quotes 都注入，不打网络。
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from paper_trading.data import realtime as rt
from paper_trading.data.db_manager import DataDBManager
from paper_trading.data.raw_feed import today_reference_state
from paper_trading.data.source import BarSource
from paper_trading.integration.rq_engine import costs

from paper_trading.models import TradingConfig as _TC
_FEE = {k: float(getattr(_TC(), k)) for k in
        ('commission_rate', 'commission_min', 'stamp_duty_rate', 'transfer_fee_rate')}
from paper_trading.integration.rq_engine.runner import build_config, session_fingerprint
from paper_trading.integration.rq_engine.store import RawDayBarStore
from paper_trading.models import Bar

CN = timezone(timedelta(hours=8))
T = date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 18, 22, tzinfo=CN)          # 盘后采集时刻（实测场景）


def ts(day: date, hhmmss: str = "16:14:45") -> str:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return datetime(day.year, day.month, day.day, h, m, int(s), tzinfo=CN).isoformat()


def mkbar(sym, d, close, *, checked=False, amount=1e8, volume=1000):
    return Bar(symbol=sym, timestamp=datetime.combine(d, datetime.min.time()),
               open=close, high=close + 0.1, low=close - 0.1, close=close,
               volume=volume, amount=amount, adjust="raw")


@pytest.fixture()
def db(tmp_path):
    return DataDBManager(tmp_path / "data.db")


# ---------------- (a) 收盘快照新鲜度 ----------------
def test_closing_snapshot_accepted_after_close():
    """现网形状：18:22 采集、腾讯 ts 16:14（收盘后定稿）⇒ 必须算当日可用参照。"""
    q = {"price": 37.58, "ts": ts(T, "16:14:45"), "date": T.isoformat()}
    assert rt.is_closing_snapshot(q, NOW) is True
    # 180 秒那条盘中规矩会把它毙掉——这正是 T42 §4 报的自断防线
    assert rt.quote_fresh(q, NOW) is False
    assert rt.quote_usable(q, NOW) is False                        # 撮合口径不变
    assert rt.quote_usable(q, NOW, allow_closing=True) is True     # 只有盘后对照放宽


@pytest.mark.parametrize("q,why", [
    ({"price": 1.0, "ts": ts(T - timedelta(days=1), "15:30:00")}, "昨天的快照"),
    ({"price": 1.0, "ts": ts(T, "14:59:59")}, "没到收盘，不算收盘定稿"),
    ({"price": 1.0}, "没有时间戳"),
    ({"price": 1.0, "ts": ts(T, "09:30:00")}, "早盘快照"),
])
def test_closing_snapshot_rejects_wrong_shapes(q, why):
    assert rt.is_closing_snapshot(q, NOW) is False, why


def test_closing_snapshot_window_is_same_day_only():
    """窗口设计：当晚仍可用（盘后采集最晚到深夜），**跨日就作废**——
    宁可回到"没有当日参照"，也不拿昨天的收盘价冒充今天的独立参照。"""
    q = {"price": 1.0, "ts": ts(T, "15:05:00")}
    assert rt.is_closing_snapshot(q, datetime(2026, 10, 29, 23, 30, tzinfo=CN)) is True
    assert rt.is_closing_snapshot(q, datetime(2026, 10, 30, 0, 30, tzinfo=CN)) is False
    # 窗口之外（例如次日凌晨还想要昨天的快照）也必须是 False：日期已经换了
    assert rt.QUOTE_CLOSING_MAX_AGE > (datetime(2026, 10, 29, 23, 30, tzinfo=CN)
                                       - datetime(2026, 10, 29, 15, 5, tzinfo=CN)
                                       ).total_seconds()


def test_get_quotes_keeps_session_gate_for_matching_and_opens_it_only_on_request(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(codes, timeout=15.0):
        calls["n"] += 1
        return {"000651": {"code": "000651", "price": 37.58,
                           "ts": ts(T, "16:14:45"), "date": T.isoformat()}}

    monkeypatch.setattr(rt, "fetch_batch", fake_fetch)
    monkeypatch.setattr(rt, "is_trading_session", lambda now=None, holidays=None: False)
    monkeypatch.setattr(rt, "_cache", {"at": 0.0, "data": {}})

    assert rt.get_quotes(["000651"], force=True, now=NOW) == {}       # 撮合路径仍是空
    got = rt.get_quotes(["000651"], force=True, allow_closing=True, now=NOW)
    assert calls["n"] == 1 and list(got) == ["000651"]                # 盘后对照能拿到


def test_intraday_reference_uses_the_closing_path(monkeypatch):
    """`intraday_reference` 默认入口必须带上 `allow_closing=True`，否则 T50-A 等于没修。"""
    seen = {}

    def fake_get_quotes(codes, force=False, allow_closing=False):
        seen["allow_closing"] = allow_closing
        return {"000651": {"price": 37.58, "date": T.isoformat()}}

    import paper_trading.data.realtime as realtime_mod
    monkeypatch.setattr(realtime_mod, "get_quotes", fake_get_quotes)
    from paper_trading.data import raw_feed
    out = raw_feed.intraday_reference(["000651"])
    assert seen["allow_closing"] is True
    assert out == {"000651": {"price": 37.58, "date": T.isoformat()}}


# ---------------- (a)+(c) 合起来的因果链 ----------------
def test_post_close_collection_then_engine_serves_the_bar(db):
    """盘后采集用收盘快照对照 ⇒ checked=1 ⇒ 引擎取数口放行（裁决 1 要的就是这条链）。"""
    from paper_trading.data import raw_feed

    bars = [mkbar("000651", T, 37.58)]
    res = raw_feed.sync_raw(db, ["000651"], days=30, today=T,
                            now=NOW.replace(tzinfo=None),
                            fetch=lambda s, d: bars,
                            reference=lambda syms, days: {},
                            intraday={"000651": {"price": 37.58, "date": T.isoformat()}})
    assert res["written"] == 1 and res["intraday_checked"] == 1
    src = BarSource(db, source="local")
    served = src.engine_raw_bars("000651", limit=5, today=T)
    assert len(served) == 1 and src.last_engine_reject is None


def test_engine_refuses_today_bar_without_independent_reference(db):
    """单源采集（没有当日参照）⇒ 引擎必须拒喂，并给出可读原因。"""
    db.upsert_raw_bars([mkbar("000651", T, 37.58)], source="akshare:sina:raw")
    src = BarSource(db, source="local")
    assert src.engine_raw_bars("000651", limit=5, today=T) == []
    assert src.last_engine_reject == "today_bar_unchecked: 2026-10-29"


def test_today_reference_state_three_shapes(db):
    assert today_reference_state(db, "000651", today=T)["state"] == "no_today_bar"
    db.upsert_raw_bars([mkbar("000651", T, 37.58)], source="x")
    assert today_reference_state(db, "000651", today=T)["state"] == "unchecked"
    db.upsert_raw_bars([mkbar("000651", T, 37.58)], source="x",
                       checked_dates={T.isoformat()})
    assert today_reference_state(db, "000651", today=T)["state"] == "checked"


# ---------------- 适配器侧（真正的喂引擎路径）----------------
def test_store_drops_symbol_without_reference_and_records_reason(tmp_path):
    """一只当日有独立参照、一只没有 ⇒ 只供有参照的那只，被挡的记进 gate_rejected。

    每只都给 T-1 + T 两根：band 需要前收盘（G-T13-3），否则先撞带闸。
    """
    db2 = DataDBManager(tmp_path / "d2.db")
    db2.upsert_raw_bars([mkbar("600036", T - timedelta(days=1), 41.0),
                         mkbar("600036", T, 42.94)], source="x",
                        checked_dates={T.isoformat()})
    db2.upsert_raw_bars([mkbar("000651", T - timedelta(days=1), 36.0),
                         mkbar("000651", T, 37.58)], source="x")   # 当日没参照
    st = RawDayBarStore.from_data_db(db2, ["000651", "600036"], days=30, today=T)
    assert st.order_book_ids() == ["600036.XSHG"]
    assert st.gate_rejected == {"000651": "today_bar_unchecked"}


def test_store_gate_can_be_disabled_explicitly(tmp_path):
    db = DataDBManager(tmp_path / "d3.db")
    db.upsert_raw_bars([mkbar("000651", T - timedelta(days=1), 36.0),
                        mkbar("000651", T, 37.58)], source="x")
    st = RawDayBarStore.from_data_db(db, ["000651"], days=30, today=T,
                                     require_today_reference=False)
    assert st.order_book_ids() == ["000651.XSHE"] and st.gate_rejected == {}


def test_config_and_fingerprint_carry_the_gate():
    cfg_on = build_config(today="2026-10-29", data_db="d.db", bundle="b", symbols=["000651"],
                          fee=_FEE)
    assert cfg_on["mod"]["ptpaper"]["require_today_reference"] is True
    cfg_off = build_config(today="2026-10-29", data_db="d.db", bundle="b",
                           symbols=["000651"], require_today_reference=False, fee=_FEE)
    assert cfg_off["mod"]["ptpaper"]["require_today_reference"] is False
    assert session_fingerprint(cfg_on) != session_fingerprint(cfg_off), \
        "闸的开/关会改变账本语义，必须进会话指纹（G-T13-5）"


def test_costs_module_still_untouched_by_t50():
    """回归护栏：T50 不许把 T38 的费率口径带偏。

    T51 起这护栏换了写法：倍率仍等于现网率/框架率（0.00025/0.0008=0.3125），
    但**数字来自 `TradingConfig`**，`costs.py` 自己不再持有费率常量。
    """
    from paper_trading.models import TradingConfig
    from paper_trading.utils import fees as ufees

    rate = TradingConfig().commission_rate
    c = costs.stock_cost_mod_config(**{k: v for k, v in ufees.fee_dict(TradingConfig()).items()
                                       if k != "transfer_fee_rate"})
    assert c["stock_commission_multiplier"] == pytest.approx(rate / 0.0008)
    assert c["stock_commission_multiplier"] == pytest.approx(0.3125)
    assert not hasattr(costs, "TRANSFER_FEE_RATE"), "costs.py 不许再自带费率数字"
