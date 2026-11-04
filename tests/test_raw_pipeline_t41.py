"""T41 raw 管道加固测试（Batch 9 批复 2 的三缺陷，逐条对应）。

① 参照窗口必须覆盖采集窗口（旧坑：采集 `days*2+10` 自然日 vs 母库 `LIMIT days` 交易日
   ⇒ NEC 实测 590 行只验了 400 行）；
② `raw_flip_watch` 逐日台账（旧坑：只看"最后 checked 距今"，中段一批未验料
   让 `data raw-coverage --gate` 报 `violations=[]`）；
③ `--no-mother-ref` 必须真不读母库（旧坑：CLI 留 `reference=None`，
   而 `None` 的语义是"用默认=读母库"，旗子形同虚设）。

全部离线：fetch/reference/mother 都注入假源，母库用临时 sqlite 或干脆 monkeypatch。
"""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from paper_trading.data import raw_feed
from paper_trading.data.db_manager import DataDBManager
from paper_trading.integration import mother as mother_mod
from paper_trading.models import Bar

TODAY = date(2026, 10, 28)
PRE_CLOSE = datetime(2026, 10, 28, 14, 0)      # naive = 北京墙钟，未收盘 ⇒ 不启用当日跨源


def mkbar(sym, d, close, *, amount=1e8, volume=1000):
    return Bar(symbol=sym, timestamp=datetime.combine(d, datetime.min.time()),
               open=close, high=close + 0.1, low=close - 0.1, close=close,
               volume=volume, amount=amount, adjust="raw")


@pytest.fixture()
def db(tmp_path):
    return DataDBManager(tmp_path / "data.db")


def weekdays_back(n, end=TODAY):
    """最近 n 个交易日（按工作日近似，够用于纯函数断言）。"""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


# ---------- ① 采集/参照/审计同一个窗口 ----------
def test_fetch_window_is_the_single_definition_of_the_span():
    start, end = raw_feed.fetch_window(40, TODAY)
    assert end == TODAY
    assert start == TODAY - timedelta(days=40 * 2 + 10)
    # 常量集中：换算法只改一处，采集/参照/审计三处一起跟着走
    s2, _ = raw_feed.fetch_window(40, TODAY)
    assert s2 == start


def test_sync_raw_asks_mother_with_since_aligned_to_fetch(monkeypatch, db):
    seen = {}

    def fake_map(codes, days=30, mother=None, ttl=0.0, since=None):
        seen["since"] = since
        seen["days"] = days
        return {str(c): {} for c in codes}

    monkeypatch.setattr(mother_mod, "raw_close_map", fake_map)
    bars = [mkbar("000792", d, 22.0) for d in weekdays_back(10)]
    raw_feed.sync_raw(db, ["000792"], days=30, today=TODAY, now=PRE_CLOSE,
                      fetch=lambda s, d: bars)
    assert seen["since"] == raw_feed.fetch_window(30, TODAY)[0].isoformat(), \
        "参照必须按采集窗口的下界要数据，不能再各算各的 LIMIT"


def test_aligned_window_verifies_every_bar_but_short_window_leaves_holes(monkeypatch, db):
    """行为级证据：同一批 bar，参照窗口取齐 ⇒ 全验；只给最近 40 个交易日 ⇒ 有洞。

    后半段就是这个回归用例的"变异检查"：谁把窗口改回 `LIMIT days`，这里立刻红。
    """
    days = 30
    start, _end = raw_feed.fetch_window(days, TODAY)
    # 真实管道里 bar 不可能早于 since（akshare 的 start_date 就是它），测试取同一窗口
    bars_dates = [d for d in weekdays_back(48) if start <= d <= TODAY]
    bars = [mkbar("000792", d, 22.0) for d in bars_dates]
    all_refs = {d.isoformat(): {"close_raw": 22.0} for d in bars_dates}

    def aligned(codes, days=30, mother=None, ttl=0.0, since=None):
        assert since == start.isoformat()
        return {str(c): {k: v for k, v in all_refs.items()
                         if k >= (since or "0000-00-00")} for c in codes}

    def truncated(codes, days=30, mother=None, ttl=0.0, since=None):
        # 旧实现：只看 days（交易日行数），since 被忽略
        keep = sorted(all_refs)[-days:]
        return {str(c): {k: all_refs[k] for k in keep} for c in codes}

    monkeypatch.setattr(mother_mod, "raw_close_map", aligned)
    res = raw_feed.sync_raw(db, ["000792"], days=days, today=TODAY, now=PRE_CLOSE,
                            fetch=lambda s, d: list(bars))
    assert res["results"][0]["checked"] == len(bars)
    assert res["results"][0]["no_reference"] == 0
    assert res["ref_window"]["start"] == start.isoformat()
    assert res["flip_watch"]["unflipped_days"] == 0

    db2 = DataDBManager(db.db_path.with_name("data2.db"))
    monkeypatch.setattr(mother_mod, "raw_close_map", truncated)
    res2 = raw_feed.sync_raw(db2, ["000792"], days=days, today=TODAY, now=PRE_CLOSE,
                             fetch=lambda s, d: list(bars))
    assert res2["results"][0]["checked"] == days, "旧口径的洞必须能被这条用例看见"
    assert res2["results"][0]["no_reference"] == len(bars) - days


def test_mother_raw_close_map_since_filters_by_date(tmp_path):
    """母库侧：`since` 走日期下界，`days` 仍是行数上限（老调用者行为不变）。"""
    md = tmp_path / "dash"
    (md / "src").mkdir(parents=True)
    (md / "data" / "db").mkdir(parents=True)
    p = md / "data" / "db" / "stock_dashboard.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE kline_daily (code TEXT, trade_date TEXT, close_raw REAL,"
                " volume REAL, amount REAL)")
    rows = [("000792", d.isoformat(), 22.0, 1.0, 1.0) for d in weekdays_back(60)]
    rows += [("600519", d.isoformat(), 1500.0, 1.0, 1.0) for d in weekdays_back(60)]
    con.executemany("INSERT INTO kline_daily VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()

    full = mother_mod.raw_close_map(["000792"], days=60, mother=md)
    assert len(full["000792"]) == 60
    limited = mother_mod.raw_close_map(["000792"], days=30, mother=md)
    assert len(limited["000792"]) == 30
    cut = min(d.isoformat() for d in weekdays_back(60)[20:])
    since_map = mother_mod.raw_close_map(["000792"], mother=md, since=cut)
    assert since_map["000792"] and all(k >= cut for k in since_map["000792"])
    assert len(since_map["000792"]) == 40


# ---------- ② 逐日台账 ----------
def test_raw_date_flags_returns_per_day_ledger(db):
    db.upsert_raw_bars([mkbar("000792", date(2026, 10, 26), 22.30),
                        mkbar("000792", date(2026, 10, 27), 22.21),
                        mkbar("000792", TODAY, 22.10)],
                       checked_dates={"2026-10-27"})
    f = db.raw_date_flags("000792")
    assert f == {"2026-10-26": 0, "2026-10-27": 1, "2026-10-28": 0}
    assert db.raw_date_flags("000792", since="2026-10-27") == \
        {"2026-10-27": 1, "2026-10-28": 0}
    assert db.raw_date_flags("000792", until="2026-10-26") == {"2026-10-26": 0}
    assert db.raw_date_flags("999999") == {}


def _seed(db, sym, dates, checked):
    db.upsert_raw_bars([mkbar(sym, d, 22.0) for d in dates], checked_dates=set(checked))


def test_flip_watch_detects_mid_history_unverified_days(db):
    """盲区本体：最新 bar 与最后 checked 都"看起来正常"，中段一批日子从没验过。

    旧实现只看 `MAX(timestamp) WHERE checked=1` 距今几个交易日 ⇒ 这种情况
    `violations=[]`；逐日台账必须把它点出来并停线。
    """
    ds = [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22),
          date(2026, 10, 23), date(2026, 10, 26), date(2026, 10, 27)]
    _seed(db, "000792", ds, {"2026-10-27"})            # 只有最新一根 checked
    ref = {d.isoformat(): {"close_raw": 22.0} for d in ds}
    w = raw_feed.raw_flip_watch(db, ["000792"], today=date(2026, 10, 27),
                                ref_asof={"000792": "2026-10-27"},
                                ref_days={"000792": set(ref)},
                                since=date(2026, 10, 19))
    daily = w["per_symbol"]["000792"]["daily"]
    assert daily["unflipped"] == ["2026-10-20", "2026-10-21", "2026-10-22",
                                 "2026-10-23", "2026-10-26"]
    assert w["stop_line"] is True and w["violations"] == ["000792"]
    assert w["unflipped_days"] == 5
    assert w["per_symbol"]["000792"]["reason"] == "checked_not_flipped"


def test_flip_watch_mother_gap_is_warning_not_stop(db):
    """母库自己缺某天（我们无从验料）⇒ 显式 `no_reference_available`，不停线但必须可见。"""
    ds = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]
    _seed(db, "000792", ds, {"2026-10-26", "2026-10-28"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=TODAY,
                                ref_asof={"000792": "2026-10-28"},
                                ref_days={"000792": {"2026-10-26", "2026-10-28"}},
                                since=date(2026, 10, 26))
    d = w["per_symbol"]["000792"]["daily"]
    assert d["no_reference_available"] == ["2026-10-27"] and d["unflipped"] == []
    assert w["stop_line"] is False
    assert "000792" in w["warnings"]
    assert w["no_reference_days"] == 1 and w["audit_complete"] is True


def test_flip_watch_null_close_raw_does_not_count_as_reference(db):
    """母库当日 `close_raw` 是 NULL（§5.5 裁决 2-A 存在的原因）⇒ 不算有参照。"""
    ds = [date(2026, 10, 26), date(2026, 10, 27)]
    _seed(db, "000792", ds, {"2026-10-26"})
    refs = {"000792": {"2026-10-26": {"close_raw": 22.0},
                       "2026-10-27": {"close_raw": None}}}
    have = raw_feed._ref_date_set(refs)
    assert have == {"000792": {"2026-10-26"}}
    w = raw_feed.raw_flip_watch(db, ["000792"], today=date(2026, 10, 27),
                                ref_asof={"000792": "2026-10-26"},
                                ref_days=have, since=date(2026, 10, 26))
    d = w["per_symbol"]["000792"]["daily"]
    assert d["unflipped"] == [] and w["stop_line"] is False


def test_flip_watch_today_row_is_never_a_stop(db):
    """当日那根允许还没翻转（裁决 2-B 的明文），逐日台账也不能把它算成 unflipped。"""
    ds = [date(2026, 10, 26), date(2026, 10, 27), TODAY]
    _seed(db, "000792", ds, {"2026-10-26", "2026-10-27"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=TODAY,
                                ref_asof={"000792": "2026-10-28"},
                                ref_days={"000792": {"2026-10-26", "2026-10-27",
                                                     "2026-10-28"}},
                                since=date(2026, 10, 26))
    d = w["per_symbol"]["000792"]["daily"]
    assert d["today_pending"] == ["2026-10-28"] and d["unflipped"] == []
    assert w["stop_line"] is False and w["violations"] == []


def test_flip_watch_without_ref_days_marks_audit_incomplete(db):
    """参照存在性未知时：不能静默放行，也不能把母库的缺口算成我们的锅 ⇒ 显式 incomplete。"""
    ds = [date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)]
    _seed(db, "000792", ds, {"2026-10-26"})
    w = raw_feed.raw_flip_watch(db, ["000792"], today=date(2026, 10, 26),
                                ref_asof={"000792": "2026-10-26"},
                                since=date(2026, 10, 22))
    d = w["per_symbol"]["000792"]["daily"]
    assert sorted(d["audit_incomplete"]) == ["2026-10-22", "2026-10-23"]
    assert w["stop_line"] is False and w["audit_complete"] is False
    assert w["incomplete_days"] == 2
    assert w["per_symbol"]["000792"]["reason"] == "audit_incomplete"


def test_flip_watch_aggregates_across_symbols(db):
    _seed(db, "000792", [date(2026, 10, 26), date(2026, 10, 27)], {"2026-10-27"})
    _seed(db, "600519", [date(2026, 10, 26), date(2026, 10, 27)],
          {"2026-10-26", "2026-10-27"})
    w = raw_feed.raw_flip_watch(
        db, ["000792", "600519"], today=date(2026, 10, 27),
        ref_asof={"000792": "2026-10-27", "600519": "2026-10-27"},
        ref_days={"000792": {"2026-10-26", "2026-10-27"},
                  "600519": {"2026-10-26", "2026-10-27"}},
        since=date(2026, 10, 26))
    assert w["audited_days"] == 4 and w["verified_days"] == 3
    assert w["unflipped_days"] == 1 and w["violations"] == ["000792"]


def test_flip_watch_with_mother_uses_aligned_window_and_survives_mother_loss(monkeypatch, db):
    """`flip_watch_with_mother` 是 CLI 的入口：母库炸了也不能假装全绿。"""
    _seed(db, "000792", [date(2026, 10, 26), date(2026, 10, 27)], set())

    def boom(*a, **k):
        raise RuntimeError("母库不可达")

    monkeypatch.setattr(mother_mod, "raw_close_map", boom)
    w = raw_feed.flip_watch_with_mother(db, ["000792"], today=date(2026, 10, 27), days=30)
    assert w["audit_complete"] is False
    # 10-26 存在性未知 → incomplete；10-27 是"今天" → today_pending（裁决 2-B 允许）
    assert w["incomplete_days"] == 1
    d = w["per_symbol"]["000792"]["daily"]
    assert d["audit_incomplete"] == ["2026-10-26"] and d["today_pending"] == ["2026-10-27"]
    # 参照源整条断线 + 窗口里有未验行 ⇒ 停线，但原因归到"参照源不可用"（不是我们的锅，
    # 也不能拿它当没事发生——钱类数据宁可停也不单源裸奔）
    assert w["stop_line"] is True and w["violations"] == ["000792"]
    assert w["per_symbol"]["000792"]["reason"] == "reference_source_unavailable"


def test_flip_watch_with_mother_reads_the_inner_reference_dict(monkeypatch, db):
    """形状回归：`raw_close_map` 返回 `{code: {date: row}}`，闸要用**内层**。

    写成外层时（真机实测发生过）`max(v)` 拿到的是标的代码而不是日期，
    `ds in have` 永远为假 ⇒ 两个方向的错同时发生：
    母库明明有的未验日不再算 unflipped（漏报），
    母库单纯滞后的标的却被 `ck_lag` 规则误判停线（误报）。
    """
    ds = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]
    _seed(db, "000792", ds, {"2026-10-28"})               # 10-26/27 母库有却未验
    _seed(db, "600036", ds, {"2026-10-26", "2026-10-28"})  # 10-27 母库还没出 ⇒ 等它

    def fake_map(codes, days=30, mother=None, ttl=0.0, since=None):
        out = {}
        for c in codes:
            c = str(c)
            if c == "000792":
                out[c] = {d.isoformat(): {"close_raw": 22.0} for d in ds}
            else:
                out[c] = {"2026-10-26": {"close_raw": 41.0}}
        return out

    monkeypatch.setattr(mother_mod, "raw_close_map", fake_map)
    w = raw_feed.flip_watch_with_mother(db, ["000792", "600036"],
                                        today=date(2026, 10, 28), days=30)
    a = w["per_symbol"]["000792"]
    assert a["ref_asof"] == "2026-10-28", "ref_asof 必须是日期，不是标的代码"
    assert a["daily"]["unflipped"] == ["2026-10-26", "2026-10-27"]
    assert a["stop"] is True
    b = w["per_symbol"]["600036"]
    assert b["ref_asof"] == "2026-10-26"
    assert b["daily"]["waiting_reference"] == ["2026-10-27"] and b["daily"]["unflipped"] == []
    assert b["stop"] is False
    assert w["violations"] == ["000792"]


def test_flip_watch_with_mother_asks_reference_for_the_whole_window(monkeypatch, db):
    seen = {}

    def fake_map(codes, days=30, mother=None, ttl=0.0, since=None):
        seen["since"] = since
        seen["codes"] = list(codes)
        return {str(c): {} for c in codes}

    monkeypatch.setattr(mother_mod, "raw_close_map", fake_map)
    _seed(db, "000792", [date(2026, 10, 27)], {"2026-10-27"})
    w = raw_feed.flip_watch_with_mother(db, ["000792"], today=date(2026, 10, 28), days=30)
    assert seen["since"] == raw_feed.fetch_window(30, date(2026, 10, 28))[0].isoformat()
    assert seen["codes"] == ["000792"]
    assert w["audit_complete"] is True                    # 母库回空 dict ≠ 读失败


# ---------- ③ 单源语义 ----------
def test_reference_for_maps_the_flag_to_a_real_no_read():
    assert raw_feed.reference_for(True) is raw_feed.NO_REFERENCE
    assert raw_feed.reference_for(False) is None
    assert raw_feed.NO_REFERENCE(["000792", "600519"], 30) == {}


def test_no_mother_ref_never_touches_mother(monkeypatch, db):
    """带 `--no-mother-ref` 的语义 = 一个字节都不读母库；旧实现会回退去读。"""
    def boom(*a, **k):
        raise AssertionError("单源模式不该读母库（旧实现正是读在这里）")

    monkeypatch.setattr(mother_mod, "raw_close_map", boom)
    bars = [mkbar("000792", d, 22.0) for d in (date(2026, 10, 26), date(2026, 10, 27))]
    res = raw_feed.sync_raw(db, ["000792"], days=30, today=TODAY, now=PRE_CLOSE,
                            reference=raw_feed.reference_for(True),
                            fetch=lambda s, d: bars)
    assert res["written"] == 2 and not res["rejected"]
    assert res["results"][0]["checked"] == 0
    assert res["ref_window"]["single_source"] is True
    assert res["ref_window"]["symbols_with_ref"] == 0
    with db._connect() as conn:
        rows = conn.execute("SELECT timestamp, checked FROM daily_bars_raw").fetchall()
    assert [r["checked"] for r in rows] == [0, 0]


def test_single_source_still_writes_and_is_auditable(db):
    """单源不判死（当日靠 T+1 翻转闸兜），但台账必须显示这些行没独立参照。"""
    bars = [mkbar("000792", d, 22.0) for d in (date(2026, 10, 26), date(2026, 10, 27))]
    raw_feed.sync_raw(db, ["000792"], days=30, today=TODAY, now=PRE_CLOSE,
                      reference=raw_feed.NO_REFERENCE, fetch=lambda s, d: bars)
    w = raw_feed.raw_flip_watch(db, ["000792"], today=date(2026, 10, 27),
                                ref_asof={}, ref_days=None, since=date(2026, 10, 26))
    d = w["per_symbol"]["000792"]["daily"]
    assert d["verified"] == 0
    assert d["audit_incomplete"] == ["2026-10-26"]      # 10-26 没有独立参照，显式可见
    assert d["today_pending"] == ["2026-10-27"]
    assert w["incomplete_days"] == 1 and w["audit_complete"] is False
    # 单源模式的后果要**说出来**：参照源不可用 ⇒ 停线，不是"写了就算数"
    assert w["stop_line"] is True
    assert w["per_symbol"]["000792"]["reason"] == "reference_source_unavailable"
