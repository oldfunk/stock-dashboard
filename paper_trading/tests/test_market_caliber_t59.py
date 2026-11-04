"""T59/T60 口径测试：6 位代码 → 交易所，五个调用点必须共用同一张段表。

现网实据（2026-11-03，NEC）：池里 11-02 新进 `920156`（海昌智能，北交所 920 新段），
旧写法四处各自判市且**全错**——

* `minute._market_prefix` → `sh` → 新浪 5 分钟线 0 行 ⇒ 盘中采集每 15 分钟报
  "源无这些 bar"，`op_log[minute:gap]` 一天刷 92 条；
* `realtime.tc_symbol` → `sz` → 腾讯实时回空字段 ⇒ 该标的永远没有报价；
* `store.to_order_book_id` → `920156.XSHG` ⇒ 挂错交易所；
* `bands.band_for` → 主板 10% ⇒ 北交所真实涨跌幅是 30%，band 由我们供
  （rqalpha 只读 `limit_up/limit_down`，缺/错就是错判涨跌停）。

同一时刻实测：`bj920156` 新浪实时 39 字段、腾讯 87 字段、新浪 5 分钟 48 根；
`sh920156`/`sz920156` 三口全空。所以这里的判据不是风格，是钱类口径。
"""
import pytest

from paper_trading.data import minute as mn
from paper_trading.data.realtime import tc_symbol
from paper_trading.integration.rq_engine.bands import BAND_BSE, BAND_GROWTH, BAND_MAIN
from paper_trading.integration.rq_engine.bands import band_for
from paper_trading.integration.rq_engine.store import to_order_book_id, to_symbol
from paper_trading.utils import markets

# (代码, 期望市场, 期望 order_book_id, 期望涨跌幅档, 这一行在防什么)
SEGMENTS = [
    ("600519", "sh", "600519.XSHG", BAND_MAIN, "沪市主板"),
    ("601398", "sh", "601398.XSHG", BAND_MAIN, "沪市主板"),
    ("688981", "sh", "688981.XSHG", BAND_GROWTH, "科创板 20%"),
    ("900901", "sh", "900901.XSHG", BAND_MAIN, "沪市 B 股：9 字头但不是北交所"),
    ("000651", "sz", "000651.XSHE", BAND_MAIN, "深市主板"),
    ("002192", "sz", "002192.XSHE", BAND_MAIN, "深市主板"),
    ("300750", "sz", "300750.XSHE", BAND_GROWTH, "创业板 20%"),
    ("430047", "bj", "430047.BJ", BAND_BSE, "北交所老段"),
    ("830799", "bj", "830799.BJ", BAND_BSE, "北交所老段"),
    ("920156", "bj", "920156.BJ", BAND_BSE, "北交所 920 新段（本次现网踩坑的那只）"),
]


@pytest.mark.parametrize("code,ex,obi,band,why", SEGMENTS)
def test_five_call_sites_agree_on_one_table(code, ex, obi, band, why):
    """五个调用点对同一只票必须给出同一个市场、同一个交易所、同一档涨跌幅。"""
    assert markets.exchange_of(code) == ex, why
    assert mn._market_prefix(code) == ex, why                  # 新浪分钟线口
    assert tc_symbol(code) == f"{ex}{code}", why               # 腾讯/新浪实时口
    assert to_order_book_id(code) == obi, why                  # rqalpha 标的口
    assert band_for(code) == band, why                         # 涨跌停带口


def test_bj_segment_table_is_single_source_not_copies():
    """bands 用的段表必须就是 markets 那张（防止再出现"两份表各自漂移"）。"""
    from paper_trading.integration.rq_engine import bands

    assert bands.BSE_PREFIXES is markets.BJ_SEGMENTS


def test_920156_regression_what_production_got_wrong():
    """把 2026-11-03 现网那一次错法逐条钉住（改回旧写法这里就红）。"""
    assert markets.exchange_of("920156") == "bj"
    assert mn._market_prefix("920156") != "sh"        # 旧写法：新浪取 0 行
    assert tc_symbol("920156") != "sz920156"          # 旧写法：腾讯回空
    assert to_order_book_id("920156") != "920156.XSHG"
    assert band_for("920156") == BAND_BSE != BAND_MAIN


def test_sina_minute_url_uses_the_fixed_prefix(monkeypatch):
    """采集真正拼出来的 URL 必须带 bj920156——前缀对了但拼接错了照样取不到。"""
    seen = {}

    def fake_get(url, timeout=20, headers=None):
        seen["url"] = url
        return ("[{\"day\":\"2026-11-03 10:35:00\",\"open\":\"36.0\",\"high\":\"36.2\","
                "\"low\":\"35.9\",\"close\":\"36.1\",\"volume\":\"1200\"}]")

    monkeypatch.setattr(mn, "_http_get", fake_get)
    rows = mn.fetch_minute_sina("920156", scale=5, datalen=48)
    assert "symbol=bj920156" in seen["url"], seen["url"]
    assert len(rows) == 1 and rows[0]["ts"] == "2026-11-03T10:35"


def test_unrecognised_code_falls_back_to_sh_like_before():
    """认不出来一律 sh：与收敛前的历史契约一致，本次改动不附带行为变化。"""
    assert markets.exchange_of("") == "sh"
    assert markets.exchange_of("ABCDEF") == "sh"
    assert mn._market_prefix("") == "sh"


def test_order_book_id_round_trip():
    assert to_symbol("920156.BJ") == "920156"
    assert to_symbol("600519.XSHG") == "600519"


def test_intraday_supply_reports_no_error_for_bse_after_prefix_fix(tmp_path, monkeypatch):
    """前缀修好后，`ensure_today_minutes` 对 920 段不再报"源无这些 bar"（现网每天 92 条流水的根因）。"""
    from paper_trading.data.db_manager import DataDBManager

    db = DataDBManager(tmp_path / "d.db")
    slots = mn.completed_slots("5", __import__("datetime").datetime(2026, 11, 3, 10, 37))

    def fake(sym, period, days, now):
        return [{"ts": f"2026-11-03T{s}", "open": 36.0, "high": 36.1, "low": 35.9,
                 "close": 36.0, "volume": 100.0, "amount": 3600.0} for s in slots]

    out = mn.ensure_today_minutes(db, ["920156"], day="2026-11-03",
                                  now=__import__("datetime").datetime(2026, 11, 3, 10, 37),
                                  fetch=fake)
    assert out["errors"] == [] and out["per_symbol"]["920156"]["missing"] == []
    assert out["written"] == len(slots)
