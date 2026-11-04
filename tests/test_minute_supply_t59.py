"""T59：盘中分钟供给闭环——采集器的留痕语义与心跳（全部 monkeypatch，不打外网）。

现网实据（2026-11-03，NEC）：`deploy/m3_minute_intraday.sh` 每 15 分钟一跳，
`920156`（北交所 920 段被错判成沪市）每跳都失败一次 ⇒ 一个上午落进账本审计流水
`op_log[minute:gap]` **92 条**；心跳 `minute` 因此 ok=False。
真故障会被这种刷屏埋掉，所以这里钉两件事：

1. **同日同症状只落一条**流水，症状集合一变立刻再落（不静默、不刷屏）；
2. 采集器每跳都写 `minute` 心跳（死了要能响）。
"""
import json
import sqlite3
import sys

import pytest

import paper_trading.cli as hb
from paper_trading.data import minute as mn


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """临时库 + 临时 cwd（心跳落 cwd/var），并保证不打外网。"""
    monkeypatch.chdir(tmp_path)
    args = ["cli", "--data-db", str(tmp_path / "d.db"), "--account-db",
            str(tmp_path / "a.db"), "--config", str(tmp_path / "nope.yaml")]
    return tmp_path, args


def _run(args, argv_tail):
    sys.argv = args + argv_tail
    try:
        hb.main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise AssertionError(f"cli 退出码 {exc.code}") from exc
    return


def _gap_rows(account_db):
    with sqlite3.connect(f"file:{account_db}?mode=ro", uri=True) as c:
        return c.execute("SELECT count(*) FROM op_log WHERE action='minute:gap'").fetchone()[0]


def test_gap_fingerprint_is_sorted_and_day_scoped():
    a = mn.gap_fingerprint("2026-11-03", ["920156", "000426"])
    b = mn.gap_fingerprint("2026-11-03", ["000426", "920156"])
    assert a == b                                   # 顺序不该造出第二条流水
    assert a != mn.gap_fingerprint("2026-11-02", ["920156", "000426"])   # 换日要重新留痕
    assert a != mn.gap_fingerprint("2026-11-03", ["920156"])             # 症状集合变了


def test_intraday_collect_logs_each_symptom_once(sandbox, monkeypatch, capsys):
    """连跑三跳（同一症状）⇒ 只落 1 条流水；症状变了 ⇒ 立刻再落 1 条。"""
    tmp, args = sandbox
    fail = {"920156"}

    def fake_fetch(symbol, period="5", days=3, now=None):
        """坏标的返回空（=源无 bar ⇒ 记 error），好标的只给头三根（故意留缺口）。"""
        if symbol in fail:
            return []                      # 源无该标的 ⇒ 这只进 errors
        return [{"ts": f"{now.date().isoformat()}T{s}", "open": 1.0, "high": 1.0,
                 "low": 1.0, "close": 1.0, "volume": 1.0, "amount": 1.0}
                for s in mn.completed_slots("5", now)]   # 健康的给满 ⇒ 不进 errors

    monkeypatch.setattr(mn, "fetch_minute", fake_fetch)
    argv = ["minute", "sync", "--intraday", "--symbols", "920156", "000426", "--json"]
    # 第一跳：只有 920156 坏 ⇒ 落一条；第二、三跳同症状 ⇒ 不再刷流水
    for _ in range(3):
        _run(args, argv)
    assert _gap_rows(tmp / "a.db") == 1, "同一症状三跳只许一条审计流水"

    # 症状集合扩大 ⇒ 必须再落一条。注意要挑一只**库里还没有料**的标的：
    # 已齐的标的会被"跳过打接口"那条优化挡掉，根本不产生 error（那是正确行为）。
    argv2 = ["minute", "sync", "--intraday", "--symbols", "920156", "000426",
             "600519", "--json"]
    fail.add("600519")
    _run(args, argv2)
    assert _gap_rows(tmp / "a.db") == 2, "症状集合变了就必须重新留痕"


def test_intraday_collect_writes_minute_heartbeat(sandbox, monkeypatch, capsys):
    """每跳都写心跳：采集器停了，看门狗要能响（接管③的静默故障项）。"""
    tmp, args = sandbox
    monkeypatch.setattr(mn, "fetch_minute",
                        lambda symbol, period="5", days=3, now=None: [])
    _run(args, ["minute", "sync", "--intraday", "--symbols", "002192", "--json"])
    hb_file = tmp / "var" / "heartbeat.json"
    assert hb_file.exists(), "盘中采集必须留心跳"
    data = json.loads(hb_file.read_text(encoding="utf-8"))
    assert "minute" in data and data["minute"]["ok"] is False   # 全缺 ⇒ 不假装成功


def test_intraday_collect_heartbeat_ok_when_no_errors(sandbox, monkeypatch):
    """库已齐 ⇒ written=0 且没有 error ⇒ 心跳 ok=True（不许把"无事"报成故障）。"""
    tmp, args = sandbox
    from paper_trading.data.db_manager import DataDBManager
    from datetime import datetime

    now = datetime(2026, 11, 3, 14, 37)
    db = DataDBManager(tmp / "d.db")
    slots = mn.completed_slots("5", now)
    db.upsert_minute_bars("002192", [{"ts": f"2026-11-03T{s}", "open": 1.0, "high": 1.0,
                                      "low": 1.0, "close": 1.0, "volume": 1.0,
                                      "amount": 1.0} for s in slots], period="5")
    monkeypatch.setattr(mn, "fetch_minute",
                        lambda *a, **k: pytest.fail("库里已齐就不该再打接口"))
    # 时钟必须钉住：这一条原先用真表钟跑，11-03 下午 14:35 之后跑就自己红了
    # （库里种到 14:35，而"该有的一根"跟着真实时刻走到 15:00 ⇒ 判定为缺料去打接口）。
    # 这正是 T53 点名的"跑在某个时间点才绿的测试等于没有测试"，这次是我自己写的。
    sys.argv = args + ["minute", "sync", "--intraday", "--symbols", "002192",
                       "--now", now.isoformat(), "--json"]
    try:
        hb.main()
    except SystemExit as e:
        assert e.code in (0, None)
    data = json.loads((tmp / "var" / "heartbeat.json").read_text(encoding="utf-8"))
    assert data["minute"]["ok"] is True and data["minute"]["detail"].startswith("written=0")


def test_raw_gate_detail_reports_counts_not_a_truncated_name_list():
    """T60 的直接教训：`stale=[前5]` 让现网把"全池缺 11-02"读成"5 只未翻转"。"""
    from paper_trading.utils import m3_verify as mv

    syms = [f"60{i:04d}" for i in range(1, 39)]

    class FakeDB:
        db_path = None

    def fake_freshness(db, symbols, today=None, max_lag=1):
        return {s: {"last": "2026-10-30", "lag": 2, "stale": True} for s in symbols}

    def fake_flip(db, symbols, today=None, days=60):
        return {"violations": syms, "unflipped_days": 2, "waiting_days": 0,
                "incomplete_days": 0, "audit_complete": True, "stop_line": True}

    import paper_trading.data.raw_feed as rf
    monkey_fresh, monkey_flip = getattr(rf, "raw_freshness"), getattr(rf, "flip_watch_with_mother")
    rf.raw_freshness, rf.flip_watch_with_mother = fake_freshness, fake_flip
    try:
        r = mv.check_raw_gate(FakeDB(), syms, "2026-11-03")
    finally:
        rf.raw_freshness, rf.flip_watch_with_mother = monkey_fresh, monkey_flip
    assert r["status"] == "red"
    assert f"{len(syms)}/{len(syms)}" in r["detail"], r["detail"]
    assert "落后" in r["detail"] and "台账未翻转" in r["detail"]


def test_raw_gate_detail_separates_never_collected_from_lagged():
    """两只新入池标的（无 raw）与全池落后必须分开说——否则归因又会被带偏。"""
    from paper_trading.utils import m3_verify as mv
    import paper_trading.data.raw_feed as rf

    syms = ["600519", "000333", "000426", "920156"]

    def fake_freshness(db, symbols, today=None, max_lag=1):
        out = {}
        for s in symbols:
            out[s] = ({"last": None, "lag": None, "stale": True, "reason": "no_raw_data"}
                      if s in ("000426", "920156")
                      else {"last": "2026-10-30", "lag": 2, "stale": True})
        return out

    def fake_flip(db, symbols, today=None, days=60):
        return {"violations": ["920156"], "unflipped_days": 0, "waiting_days": 0,
                "incomplete_days": 0, "audit_complete": True, "stop_line": True}

    keep = (rf.raw_freshness, rf.flip_watch_with_mother)
    rf.raw_freshness, rf.flip_watch_with_mother = fake_freshness, fake_flip
    try:
        r = mv.check_raw_gate(None, syms, "2026-11-03")
    finally:
        rf.raw_freshness, rf.flip_watch_with_mother = keep
    d = r["detail"]
    assert "落后 2/4 只" in d and "从未采集 2 只" in d and "台账未翻转 1 只" in d, d


def test_idle_spin_stops_shouting_when_the_session_already_explained_it():
    """已定局的意图不许再被算成"静默失败"——报警失真比不报更糟。

    现网 11-03：`920156` 已被判 `INSTRUMENT_UNAVAILABLE`（rejected 带原因），
    但 gates 仍报 `IdleSpin: 计划有下单，但既无成交也无拒单原因（静默失败）`
    ——因为框架那侧确实一条单子都没提交过。
    """
    from paper_trading.integration.rq_engine.runner import check_session_gates

    ev = {"event_counts": {"BAR": 240, "SETTLEMENT": 1}, "trades": [], "rejects": []}
    plan = [{"action": "buy", "symbol": "920156", "volume": 100,
             "biz_id": "920156_BUY_100@2026-11-03T11:20:00"}]
    noisy = check_session_gates(ev, plan, handler_calls=240)
    assert any("IdleSpin" in e for e in noisy["errors"])          # 没给结论时仍要响
    ok = check_session_gates(ev, plan, handler_calls=240,
                             settled_biz={"920156_BUY_100@2026-11-03T11:20:00"})
    assert not any("IdleSpin" in e for e in ok["errors"]), ok["errors"]


# ==================================== T60：当日带的 prev_close 必须是"上一个交易日"
class _Row:
    def __init__(self, ts, close):
        self.timestamp = ts
        self.close = close


class _FakeDB:
    """只给 store 用到的两个读口：分钟线与 raw 日线。"""

    def __init__(self, raw_days, minute_days=("2026-11-03",)):
        self.raw_days, self.minute_days = raw_days, minute_days

    def get_minute_bars(self, sym, period="5", limit=1):
        return [{"ts": f"{d}T09:35:00", "open": 10.0, "high": 10.1, "low": 9.9,
                 "close": 10.0, "volume": 100.0, "amount": 1000.0}
                for d in self.minute_days]

    def get_raw_bars(self, sym, limit=400):
        return [_Row(f"{d} 00:00:00", 10.0 + i) for i, d in enumerate(self.raw_days)]


def test_minute_store_refuses_when_prev_close_would_come_from_an_older_day():
    """raw 只到 10-30 而会话日是 11-03 ⇒ 必须拒供，而不是拿 10-30 的收盘算 11-03 的带。

    现网 11-03 早上就是这个状态（15:40 采集早于源发布当日 bar ⇒ raw 缺 11-02）。
    band 是 rqalpha 判涨跌停的唯一依据（`matcher/base.py:207-218`），
    错档=错判涨跌停，而且旧写法从任何日志里都看不出来。
    """
    from paper_trading.integration.rq_engine.store import RawMinuteBarStore

    db = _FakeDB(["2026-10-29", "2026-10-30"])
    st = RawMinuteBarStore.from_data_db(db, ["002192"], today="2026-11-03")
    assert st.order_book_ids() == []
    assert "prev_close_missing" in st.gate_rejected.get("002192", ""), st.gate_rejected
    assert "2026-11-02" in st.gate_rejected["002192"]          # 说得清缺哪一天


def test_minute_store_supplies_when_prev_close_is_the_last_trading_day():
    from paper_trading.integration.rq_engine.store import RawMinuteBarStore

    db = _FakeDB(["2026-10-30", "2026-11-02"])
    st = RawMinuteBarStore.from_data_db(db, ["002192"], today="2026-11-03")
    assert st.gate_rejected == {} and st.order_book_ids() == ["002192.XSHE"]
    bars = st.get_bars("002192.XSHE")
    assert bars["limit_up"][-1] > bars["limit_down"][-1] > 0
    # 当日带 = 11-02 收盘（序列最后一个值 11.0）× 1.1（主板 10%）
    assert abs(float(bars["limit_up"][-1]) - 12.1) < 0.02


def test_minute_store_gate_can_be_disabled_for_troubleshooting():
    from paper_trading.integration.rq_engine.store import RawMinuteBarStore

    db = _FakeDB(["2026-10-29", "2026-10-30"])
    st = RawMinuteBarStore.from_data_db(db, ["002192"], today="2026-11-03",
                                     require_prev_close=False)
    assert st.order_book_ids() == ["002192.XSHE"] and st.gate_rejected == {}
