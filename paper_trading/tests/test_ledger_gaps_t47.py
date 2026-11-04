"""T47 四缺口（G1 落账 / G2 收盘 NAV / G3 底座开关 / G4 归档新开）的钱类测试。

全部用真实表结构（PaperBroker 建表）+ 真实 `DataDBManager`，不碰网络、不碰 rqalpha：
引擎侧的成交明细在这里是**手写的 evidence**——测的是"账本怎么接住外部成交"，
不是"引擎算得对不对"（那在 T32/T38/T39 的双轨对账里）。

每条都对应一条会被钱咬到的规矩，改代码前先看清它在守什么：
* G1：不重判、不猜费用拆分、守卫失败就停笔、幂等；
* G2：该记的一天不断供、不该记的两行不重复（含旧线已记的情况）；
* G3：开关必须活着（DEFAULTS 白名单）、未知值不许静默降级、语义进指纹；
* G4：先备份后动、拒绝原地抹账、T+1 冻结跟着搬、重跑不产生第二份归档。
"""
import hashlib
import os
import pathlib
import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from paper_trading.broker.paper_broker import LedgerConsistencyError, PaperBroker
from paper_trading.data.db_manager import DataDBManager
from paper_trading.engine.config import DEFAULTS, load_engine_config
from paper_trading.integration.rq_engine import ledger as ld
from paper_trading.integration.rq_engine import runner as rn
from paper_trading.models import Bar, TradingConfig

TODAY = "2026-10-29"
CASH = 100_000.0


@pytest.fixture()
def broker(tmp_path):
    return PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=CASH))


def rows(broker, table):
    with sqlite3.connect(str(broker.db_path)) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(f"SELECT * FROM {table}").fetchall()]  # noqa: S608


def mktrade(symbol="000651.XSHE", side="buy", volume=1000, price=37.58,
            commission=9.395, tax=0.0, other_fees=0.3758, **kw):
    """rqalpha 成交明细的形状（mod_entry._on_trade 出的就是这些键）。"""
    t = {"symbol": symbol, "side": side, "volume": volume, "price": price,
         "commission": commission, "tax": tax, "other_fees": other_fees}
    t["cost"] = round(commission + tax + other_fees, 4)
    t.update(kw)
    return t


# ============================================================ G1 新线写账本
def test_apply_external_fill_buy_writes_exactly_what_engine_said(broker):
    """落账用的就是引擎给的数值：不重算费、不加滑点、不改价（L1 数值红线）。"""
    order = broker.apply_external_fill(symbol="000651", direction=1, volume=1000,
                                       price=37.58, commission=9.395, transfer_fee=0.3758)
    fill = rows(broker, "fills")[-1]
    assert (fill["price"], fill["volume"], fill["direction"]) == (37.58, 1000, 1)
    assert fill["commission"] == pytest.approx(9.395)
    assert fill["transfer_fee"] == pytest.approx(0.3758)
    assert fill["stamp_duty"] == 0.0                 # 买入无印花税，不许从费率反推
    assert order.status.value == "filled"
    # 现金 = 成交额 + 佣金 + 过户费（不是 broker.config 的费率算出来的）
    assert broker.get_cash() == pytest.approx(CASH - 1000 * 37.58 - 9.395 - 0.3758)


def test_apply_external_fill_zero_fee_is_not_refilled_from_rates(broker):
    """引擎报 0 费就记 0：这里一旦套上现网费率，对账就再也找不回引擎的口径。"""
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58,
                               commission=0.0, transfer_fee=0.0)
    f = rows(broker, "fills")[-1]
    assert (f["commission"], f["transfer_fee"]) == (0.0, 0.0)


def test_apply_external_fill_avg_cost_includes_fees_like_legacy(broker):
    """口径与旧线一字不差：avg_cost 含买入费用（券商交割单口径）。"""
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58,
                               commission=9.395, transfer_fee=0.3758)
    pos = broker.get_position("000651")
    assert pos.avg_cost == pytest.approx((1000 * 37.58 + 9.395 + 0.3758) / 1000)
    assert pos.total_volume == 1000 and pos.available_volume == 0


def test_apply_external_fill_freezes_t1_with_next_trading_day(broker):
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58)
    fz = rows(broker, "t1_freeze")
    assert len(fz) == 1 and fz[0]["is_unfrozen"] == 0
    assert fz[0]["unfreeze_date"] > TODAY            # 解冻日是下一交易日，不是当天


def test_apply_external_fill_writes_entry_day_and_peak(broker):
    """T49 复核点 #2 的直接回答：新线**必须**写 `entry_day`/`peak_price`。

    `m3 verify` 的 T+1 项是按 `entry_day` 判"当日建仓不可卖"的——新线若不写这一列，
    那条检查就永远查不出问题（假绿）。G1 复用旧线的 `_execute_buy`，所以这两列
    与旧线同一条 SQL 写入；本条测试把它钉住，防止以后有人给新线另写一条落账路径。
    """
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58)
    pos = broker.get_position("000651")
    assert pos.entry_day == datetime.now().date().isoformat()
    assert pos.peak_price == pytest.approx(37.58)


def test_apply_external_fill_insufficient_cash_writes_nothing(broker):
    """付不起就抛，且**一行都不写**——半笔成交是最难归因的账本腐烂。"""
    with pytest.raises(LedgerConsistencyError, match="账本只有"):
        broker.apply_external_fill(symbol="000651", direction=1, volume=10000,
                                   price=37.58, commission=9.395)
    assert broker.get_cash() == pytest.approx(CASH)
    assert rows(broker, "fills") == [] and rows(broker, "orders") == []
    assert rows(broker, "positions") == []


def test_apply_external_fill_sell_needs_available_volume(broker):
    """T+1 当天买的卖不掉：引擎若报了超卖，守卫必须拦住而不是记成负持仓。"""
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58)
    with pytest.raises(LedgerConsistencyError, match="可卖只有"):
        broker.apply_external_fill(symbol="000651", direction=-1, volume=1000, price=38.0)
    assert rows(broker, "fills")[0]["direction"] == 1     # 只有买入那一笔


def test_apply_external_fill_sell_flow_credits_net_and_closes_position(broker):
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58)
    broker.unfreeze_t1("2099-01-01")                       # 手工解冻，模拟 T+1 之后
    cash_after_buy = broker.get_cash()
    broker.apply_external_fill(symbol="000651", direction=-1, volume=1000, price=38.00,
                               commission=9.5, stamp_duty=19.0, transfer_fee=0.38)
    assert broker.get_cash() == pytest.approx(
        cash_after_buy + 1000 * 38.0 - 9.5 - 19.0 - 0.38)
    assert broker.get_position("000651") is None           # 清仓后不留 0 股幽灵行


@pytest.mark.parametrize("kw,frag", [
    ({"volume": 0}, "非法"),
    ({"price": 0.0}, "非法"),
    ({"price": -1.0}, "非法"),
    ({"direction": 0}, "方向"),
])
def test_apply_external_fill_rejects_garbage(broker, kw, frag):
    base = {"symbol": "000651", "direction": 1, "volume": 100, "price": 37.58}
    base.update(kw)
    with pytest.raises(LedgerConsistencyError, match=frag):
        broker.apply_external_fill(**base)


# ---- 费用拆分：缺分量就不落 ----
def test_trade_fee_split_requires_all_three_components():
    t = mktrade()
    assert ld.trade_fee_split(t) == {"commission": 9.395, "stamp_duty": 0.0,
                                     "transfer_fee": 0.3758}
    for drop in ("commission", "tax", "other_fees"):
        broken = {k: v for k, v in t.items() if k != drop}
        assert ld.trade_fee_split(broken) is None          # 用 `cost` 去猜分量 = 拒落


def test_post_session_refuses_when_fee_split_missing(broker):
    """只有 `cost` 没有分量的成交：一笔都不许写（猜出来的拆分污染后面所有费用核对）。"""
    t = mktrade()
    del t["other_fees"]
    res = ld.post_session(broker, {"trades": [t, mktrade(symbol="600000.XSHG")]},
                          today=TODAY, fp="fp1")
    assert res["ok"] is False
    assert res["errors"][0]["why"] == "fee_split_missing"
    assert rows(broker, "fills") == []


def test_post_session_is_idempotent_per_biz_id(broker):
    """同会话重跑第二次必须零写入：`rq day` 被 cron 补跑时重复落账 = 凭空多成交。"""
    ev = {"trades": [mktrade()]}
    first = ld.post_session(broker, ev, today=TODAY, fp="fp-a")
    second = ld.post_session(broker, ev, today=TODAY, fp="fp-a")
    assert len(first["posted"]) == 1
    assert second["posted"] == [] and len(second["skipped"]) == 1
    assert second["skipped"][0]["why"] == "already_posted"
    assert len(rows(broker, "fills")) == 1                 # 没有第二笔
    assert broker.get_cash() == pytest.approx(CASH - 1000 * 37.58 - 9.395 - 0.3758)


def test_post_session_biz_id_survives_op_log_window(broker, monkeypatch):
    """幂等判定不能用"最近 N 条"窗口：面板问答把目标行挤出窗口就会重复落账。"""
    ld.post_session(broker, {"trades": [mktrade()]}, today=TODAY, fp="fp-a")
    monkeypatch.setattr(broker, "get_op_log", lambda limit=50: [])   # 窗口全空
    res = ld.post_session(broker, {"trades": [mktrade()]}, today=TODAY, fp="fp-a")
    assert len(res["skipped"]) == 1
    assert len(rows(broker, "fills")) == 1


def test_post_session_stops_the_batch_at_guard_failure(broker):
    """第 2 笔付不起 ⇒ 第 3 笔不写：账本停在可归因的位置。"""
    ev = {"trades": [mktrade(volume=1000),
                     mktrade(symbol="600000.XSHG", volume=100000),
                     mktrade(symbol="000001.XSHE", volume=100)]}
    res = ld.post_session(broker, ev, today=TODAY, fp="fp-b")
    assert res["ok"] is False
    assert [p["symbol"] for p in res["posted"]] == ["000651"]
    assert res["errors"][0]["why"] == "ledger_inconsistent"
    assert len(rows(broker, "fills")) == 1


def test_post_session_dry_run_writes_nothing(broker):
    res = ld.post_session(broker, {"trades": [mktrade()],
                                   "rejects": [{"symbol": "000001", "reason": "price_limit"}]},
                          today=TODAY, fp="fp-d", dry_run=True)
    assert len(res["posted"]) == 1 and res["posted"][0]["dry_run"] is True
    assert rows(broker, "fills") == [] and rows(broker, "op_log") == []
    assert broker.get_cash() == pytest.approx(CASH)


def test_post_session_normalizes_order_book_id_and_traces_rejects(broker):
    """`000651.XSHE` 落账必须是 `000651`；拒单也要留痕，否则"今天为什么没成交"不可审计。"""
    res = ld.post_session(broker, {"trades": [mktrade()],
                                   "rejects": [{"symbol": "000001.XSHE",
                                                "reason": "price_limit"}]},
                          today=TODAY, fp="fp-e")
    assert res["posted"][0]["symbol"] == "000651"
    assert rows(broker, "fills")[-1]["symbol"] == "000651"
    rejects = [o for o in rows(broker, "op_log") if o["action"] == "rq:reject"]
    assert len(rejects) == 1 and rejects[0]["ok"] == 0


# ============================================================ G2 收盘 NAV
def mkbar(sym, d, close, amount=1e8):
    return Bar(symbol=sym, timestamp=datetime.combine(d, datetime.min.time()),
               open=close, high=close, low=close, close=close,
               volume=1000, amount=amount, adjust="raw")


def test_record_close_nav_runs_even_without_trades(broker):
    """当天零成交也要有净值行：NAV 序列断供会让面板与 m3 verify 双双失真。"""
    out = ld.record_close_nav(broker, {}, today=TODAY, fp="fp-nav")
    assert out["recorded"] is True
    assert out["total_value"] == pytest.approx(CASH)
    assert out["cash"] == pytest.approx(CASH)


def test_record_close_nav_dedupes_business_day(broker):
    ld.record_close_nav(broker, {}, today=TODAY, fp="fp-a")
    second = ld.record_close_nav(broker, {}, today=TODAY, fp="fp-b")
    assert second["recorded"] is False and second["reason"] == "already_recorded"
    assert len(rows(broker, "nav_history")) == 1


def test_record_close_nav_yields_to_legacy_nav_close(broker):
    """双轨期间旧线已记过 `nav:close` ⇒ 新线不记第二行（同一业务日两行净值 = 曲线失真）。"""
    broker.log_operation("nav:close", {"symbols": 5}, True, {"date": TODAY}, CASH, CASH)
    out = ld.record_close_nav(broker, {}, today=TODAY, fp="fp-a")
    assert out == {"recorded": False, "reason": "already_recorded", "today": TODAY}
    assert rows(broker, "nav_history") == []


def test_record_close_nav_refuses_when_position_price_missing(broker):
    """缺价就拒：`get_nav` 把缺价当 0 市值，会让净值凭空腰斩（fail-closed）。"""
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58)
    out = ld.record_close_nav(broker, {}, today=TODAY)
    assert out["recorded"] is False and out["reason"] == "price_missing"
    assert out["symbols"] == ["000651"]
    assert rows(broker, "nav_history") == []


def test_record_close_nav_uses_the_same_math_as_legacy(broker):
    """NAV 公式不许自己另写一套：与 `broker.get_nav()` 逐位相同（pnl 对 initial_cash）。"""
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58)
    prices = {"000651": 40.0}
    expect = broker.get_nav(prices)
    out = ld.record_close_nav(broker, prices, today=TODAY)
    nav = rows(broker, "nav_history")[-1]
    assert nav["total_value"] == pytest.approx(expect.total_value)
    assert nav["market_value"] == pytest.approx(expect.market_value)
    assert nav["pnl"] == pytest.approx(expect.pnl)         # 自己算的那版恒为 0，正是错处


def test_close_prices_reads_only_latest_raw_bar(tmp_path):
    db = DataDBManager(tmp_path / "data.db")
    db.upsert_raw_bars([mkbar("000651", date(2026, 10, 27), 36.0),
                        mkbar("000651", date(2026, 10, 28), 37.0),
                        mkbar("000651", date(2026, 10, 29), 38.0),
                        mkbar("000651", date(2026, 10, 30), 99.0)])   # 未来日不许混进来
    assert ld.close_prices(db, ["000651", "600000.XSHG"], TODAY) == {"000651": 38.0}


# ============================================================ G3 执行底座开关
def test_executor_key_is_in_defaults_whitelist(tmp_path, monkeypatch):
    """`engine.executor` 必须列进 DEFAULTS：白名单过滤会把没列的键**静默丢掉**（T45 点过的坑）。"""
    assert "executor" in DEFAULTS and DEFAULTS["executor"] == "legacy"
    (tmp_path / "cfg.yaml").write_text("engine:\n  executor: rqalpha\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load_engine_config(config_path="cfg.yaml")["executor"] == "rqalpha"


@pytest.mark.parametrize("raw,expect", [("legacy", "legacy"), ("RQAlpha ", "rqalpha"),
                                        (None, "legacy"), ("", "legacy")])
def test_executor_from_config_defaults_and_case(raw, expect):
    assert ld.executor_from_config({"executor": raw}) == expect


def test_executor_from_config_refuses_unknown_value():
    """未知值不许降级成 legacy：静默换底座 = 用哪套撮合都不知道了。"""
    for bad in ("rq", "rqα", "legacy2", "shadow"):
        with pytest.raises(ValueError, match="legacy|rqalpha"):
            ld.executor_from_config({"executor": bad})


def test_session_fingerprint_changes_with_ledger_flag():
    """落不落现网账本是最大的语义差：必须进指纹，否则 G-T13-5 会放行混账续跑。"""
    from paper_trading.utils import fees as ufees

    common = dict(today=TODAY, data_db="data.db", bundle="b", symbols=["000651"],
                  state_dir="st", fee=ufees.fee_dict(TradingConfig()))
    fp_off = rn.session_fingerprint(rn.build_config(**common))
    fp_on = rn.session_fingerprint(rn.build_config(ledger=True, **common))
    assert fp_off != fp_on
    assert rn.build_config(**common)["mod"]["ptpaper"]["ledger"] is False   # 默认不落账


def test_trace_switch_records_once_per_day_and_executor(broker):
    assert ld.trace_switch(broker, "rqalpha", TODAY, reason="演练") is True
    assert ld.trace_switch(broker, "rqalpha", TODAY) is False        # 同日同值不重复
    assert ld.trace_switch(broker, "legacy", TODAY) is True          # 回退是另一条留痕
    traces = [o for o in rows(broker, "op_log") if o["action"] == "m3:switch"]
    assert len(traces) == 2


def test_ledger_decide_prefers_explicit_flags_over_config(broker, tmp_path, monkeypatch):
    """显式旗子 > 配置：`rq day` 是排障入口，默认不许按配置悄悄写钱类账本。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cfg.yaml").write_text("engine:\n  executor: rqalpha\n", encoding="utf-8")
    br = SimpleNamespace(broker=broker, config_path=str(tmp_path / "cfg.yaml"))
    on = SimpleNamespace(ledger=True, no_ledger=False, today=TODAY)
    off = SimpleNamespace(ledger=None, no_ledger=True, today=TODAY)
    follow = SimpleNamespace(ledger=None, no_ledger=False, today=TODAY)
    assert ld.executor_from_config(load_engine_config("cfg.yaml")) == "rqalpha"
    from paper_trading.cli import _ledger_decide
    assert _ledger_decide(off, br) is False                  # 配置说要写，旗子说不写
    assert _ledger_decide(on, br) is True
    assert _ledger_decide(follow, br) is True                # 无旗子 → 听配置
    switches = [o for o in rows(broker, "op_log") if o["action"] == "m3:switch"]
    assert len(switches) == 1                                # 只有"听配置"那次留痕


def test_ledger_decide_legacy_config_does_not_write(broker, tmp_path, monkeypatch):
    """默认（legacy）时 `rq day` 一个子都不落——现网账本仍由旧线独占。"""
    monkeypatch.chdir(tmp_path)
    br = SimpleNamespace(broker=broker, config_path=str(tmp_path / "none.yaml"))
    from paper_trading.cli import _ledger_decide
    assert _ledger_decide(SimpleNamespace(ledger=None, no_ledger=False, today=TODAY),
                          br) is False
    assert rows(broker, "fills") == []


@pytest.mark.parametrize("cfg,avail,expect_ex,expect_err", [
    ({}, True, "legacy", None),
    ({"executor": "legacy"}, False, "legacy", None),      # 旧线不需要 rqalpha
    ({"executor": "rqalpha"}, True, "rqalpha", None),
    # M3 切换后新语义（分层架构）：engine 进程只判定+入队（queue 纯 sqlite），
    # 执行由引擎 venv 的 rq tick 承担——rqalpha 不装在交易 venv 不再是拒启动
    # 理由；但队列不可写仍拒（意图无处去=比静默退回更糟的静默丢失）。
    ({"executor": "rqalpha"}, False, "rqalpha", None),
    ({"executor": "rq"}, True, "legacy", "只能是 legacy|rqalpha"),
])
def test_startup_guard_refuses_ambiguous_base(cfg, avail, expect_ex, expect_err):
    """runbook §5.2：底座在启动时校验并打印；未知值一律拒绝，不许静默降级。"""
    ex, err = ld.startup_guard(cfg, rqalpha_available=avail)
    assert ex == expect_ex
    assert (expect_err in err) if expect_err else (err is None)


def test_engine_daemon_exits_2_on_unknown_executor(tmp_path):
    """守护进程入口的退出码是运维可见契约：未知底座 ⇒ exit 2，不跑第二轮。"""
    import subprocess
    import sys

    root = str(pathlib.Path(__file__).resolve().parents[1])
    (tmp_path / "config.yaml").write_text("engine:\n  enabled: true\n"
                                          "  executor: rq-aplha\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "paper_trading.engine", "--once",
                        "--config", "config.yaml", "--data-db", "d.db",
                        "--account-db", "a.db", "--lock-file", "l.lock"],
                       cwd=str(tmp_path), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONPATH": root})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "只能是 legacy|rqalpha" in (r.stdout + r.stderr)


def test_legacy_key_absent_and_explicit_produce_identical_book(tmp_path):
    """§5.2 的回退回归：`executor: legacy` 与"根本没有这个键"必须产出逐位一致的账本。"""
    from paper_trading.cli import _ledger_decide

    books = []
    for idx, cfg_text in enumerate(("", "engine:\n  executor: legacy\n")):
        home = tmp_path / f"book{idx}"
        home.mkdir()
        (home / "config.yaml").write_text(cfg_text or "engine:\n", encoding="utf-8")
        b = PaperBroker(db_path=str(home / "paper_account.db"),
                        config=TradingConfig(initial_cash=CASH))
        br = SimpleNamespace(broker=b, config_path=str(home / "config.yaml"))
        # ① 底座判定（rq day 的落账决定）② 旧线当天的成交与收盘 NAV
        assert _ledger_decide(SimpleNamespace(ledger=None, no_ledger=False, today=TODAY),
                              br) is False
        b.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58,
                              commission=9.395, transfer_fee=0.3758)
        b.record_nav(b.get_nav({"000651": 37.9}))
        books.append({t: [_strip_clock(r) for r in rows(b, t)]
                      for t in ("account", "positions", "orders", "fills",
                                "nav_history", "t1_freeze")})
    assert books[0] == books[1]


#: 逐位比对时剥掉的"写入时刻"列：两次跑必然隔几毫秒，其余字段一个不许差
CLOCK_KEYS = ("created_at", "last_update", "filled_at", "timestamp")


def _strip_clock(row):
    return {k: v for k, v in row.items() if k not in CLOCK_KEYS}


# ============================================================ G4 归档 / 新开账
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def live_ledger(tmp_path):
    """一本"已经跑过几天"的账：有持仓、有未解冻的 T+1、有 NAV、有成交。"""
    b = PaperBroker(db_path=str(tmp_path / "paper_account.db"),
                    config=TradingConfig(initial_cash=CASH))
    b.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58,
                          commission=9.395, transfer_fee=0.3758)
    b.apply_external_fill(symbol="600000", direction=1, volume=2000, price=10.0,
                          commission=5.0, transfer_fee=0.2)
    b.unfreeze_t1("2099-01-01")                        # 两笔都先解冻
    _refreeze(b, "000651", 1000, "2026-10-30")        # 模拟"切换日刚买、还在冻结中"
    b.record_nav(b.get_nav({"000651": 38.0, "600000": 10.5}))
    return b


def _refreeze(broker, symbol, volume, unfreeze_day):
    with broker._connect() as conn:                     # noqa: SLF001 - 造现场
        conn.execute("UPDATE positions SET available_volume = available_volume - ?"
                     " WHERE symbol = ?", (volume, symbol))
        conn.execute("""INSERT INTO t1_freeze (symbol, volume, freeze_date,
                        unfreeze_date, is_unfrozen) VALUES (?, ?, ?, ?, 0)""",
                     (symbol, volume, TODAY, unfreeze_day))


def test_archive_and_reopen_produces_readonly_backup_and_keeps_book(live_ledger, tmp_path):
    src = tmp_path / "paper_account.db"
    cash_before = live_ledger.get_cash()
    out = ld.archive_and_reopen(src, tmp_path / "archive", stamp="20261029-1500",
                                allow_existing=True)
    arch = tmp_path / "archive" / "paper_account.pre-m3-20261029-1500.db"
    assert out["archive"] == str(arch) and arch.exists()
    assert out["cash"] == pytest.approx(cash_before)
    assert out["archive_rows"]["fills"] == 2 and out["archive_rows"]["nav_history"] == 1
    with sqlite3.connect(str(arch)) as c:              # 归档里旧账完整可读
        c.row_factory = sqlite3.Row
        assert c.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM nav_history").fetchone()[0] == 1
    fresh = PaperBroker(db_path=str(src), config=TradingConfig())
    assert fresh.get_cash() == pytest.approx(cash_before)
    assert {p.symbol for p in fresh.get_all_positions()} == {"000651", "600000"}
    assert len(rows(fresh, "orders")) == 0 and len(rows(fresh, "fills")) == 0  # 历史不搬
    assert (src.parent / "paper_account.db.pre-reopen-20261029-1500").exists()


def test_archive_file_is_read_only(live_ledger, tmp_path):
    ld.archive_and_reopen(tmp_path / "paper_account.db", tmp_path / "archive",
                          stamp="s1", allow_existing=True)
    arch = tmp_path / "archive" / "paper_account.pre-m3-s1.db"
    assert os.stat(arch).st_mode & 0o222 == 0          # 444：后续跑不许覆盖旧账


def test_archive_refuses_non_empty_ledger_and_leaves_it_untouched(live_ledger, tmp_path):
    """没给 --allow-existing 就动手 = 抹掉现网账。拒绝时连字节都不能变。"""
    src = tmp_path / "paper_account.db"
    before = sha(src)
    with pytest.raises(RuntimeError, match="拒绝原地重开"):
        ld.archive_and_reopen(src, tmp_path / "archive", stamp="s2")
    assert sha(src) == before                          # 账本一个字节没动
    arch = tmp_path / "archive" / "paper_account.pre-m3-s2.db"
    assert arch.exists()                               # 但冷备已经先落盘（先备份后判定）


def test_archive_is_idempotent_on_rerun(live_ledger, tmp_path):
    out1 = ld.archive_and_reopen(tmp_path / "paper_account.db", tmp_path / "archive",
                                 stamp="s3", allow_existing=True)
    arch = tmp_path / "archive" / "paper_account.pre-m3-s3.db"
    mtime, digest = arch.stat().st_mtime, sha(arch)
    out2 = ld.archive_and_reopen(tmp_path / "paper_account.db", tmp_path / "archive",
                                 stamp="s3", allow_existing=True)
    assert out1["archive_reused"] is False and out2["archive_reused"] is True
    assert arch.stat().st_mtime == mtime and sha(arch) == digest   # 不产生第二份归档


def test_archive_carries_pending_t1_freeze(live_ledger, tmp_path):
    """T+1 未解冻必须跟着搬：只搬 available_volume 会让切换日买的票永远卖不掉。"""
    out = ld.archive_and_reopen(tmp_path / "paper_account.db", tmp_path / "archive",
                                stamp="s4", allow_existing=True)
    fresh = PaperBroker(db_path=str(tmp_path / "paper_account.db"), config=TradingConfig())
    assert out["frozen"] == 1
    assert fresh.get_position("000651").available_volume == 0
    assert fresh.get_position("600000").available_volume == 2000
    assert fresh.unfreeze_t1("2099-01-01") == 1        # 还有解冻入口
    assert fresh.get_position("000651").available_volume == 1000


def test_archive_refuses_while_a_writer_holds_a_transaction(live_ledger, tmp_path):
    """cron 正在写的时候重开 = 和写入方抢同一本账；见 sidecar 就停手。"""
    src = tmp_path / "paper_account.db"
    before = sha(src)
    side = src.with_name(src.name + "-journal")
    side.write_bytes(b"")
    try:
        with pytest.raises(RuntimeError, match="正被写入"):
            ld.archive_and_reopen(src, tmp_path / "archive", stamp="s5",
                                  allow_existing=True)
    finally:
        side.unlink(missing_ok=True)
    assert sha(src) == before                          # 账本仍在原处
    assert not (tmp_path / "archive" / "paper_account.pre-m3-s5.db").exists()


def test_archive_missing_ledger_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ld.archive_and_reopen(tmp_path / "nope.db", tmp_path / "archive")


# ============================================ 顺手项：fetch-raw --start/--end（裁决 3）
def _refs(sym_days):
    return {d: {"close_raw": px} for d, px in sym_days.items()}


@pytest.fixture()
def data_db(tmp_path):
    return DataDBManager(tmp_path / "data.db")


def test_sync_raw_start_end_filters_bars_to_the_named_range(data_db):
    """`--start/--end` 就是"只写这几天"：源多给的日子不许顺手写进库。"""
    from paper_trading.data import raw_feed

    span = [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23),
            date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]
    prices = {d.isoformat(): 22.0 + i * 0.1 for i, d in enumerate(span)}
    res = raw_feed.sync_raw(
        data_db, ["000792"], days=30, today=date(2026, 10, 29),
        start=date(2026, 10, 22), end=date(2026, 10, 26),
        fetch=lambda s, d: [mkbar("000792", x, prices[x.isoformat()]) for x in span],
        reference=lambda syms, d: {"000792": _refs(prices)})
    assert res["ref_window"] == {"start": "2026-10-22", "end": "2026-10-26",
                                 "single_source": False, "symbols_with_ref": 1}
    got = [b.timestamp.date() for b in data_db.get_raw_bars("000792")]
    assert got == [date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)]


def test_sync_raw_backfill_of_past_range_skips_intraday_but_keeps_mother_ref(data_db):
    """回填历史区间没有"当天实时价"可比：不许报 intraday_missing，但母库对照照常。"""
    from paper_trading.data import raw_feed

    res = raw_feed.sync_raw(
        data_db, ["000792"], days=30, today=date(2026, 10, 29),
        now=datetime(2026, 10, 29, 16, 0),                 # 真实今天的收盘后
        start=date(2026, 10, 20), end=date(2026, 10, 21),
        fetch=lambda s, d: [mkbar("000792", date(2026, 10, 20), 22.0),
                            mkbar("000792", date(2026, 10, 21), 22.5)],
        reference=lambda syms, d: {"000792": _refs({"2026-10-20": 22.0,
                                                    "2026-10-21": 22.5})})
    assert res["intraday_enforced"] is False and res["intraday_missing"] == 0
    assert res["written"] == 2 and res["rejected"] == []


def test_sync_raw_default_window_still_enforces_intraday_after_close(data_db):
    """不传 start/end 时（默认今天）当日盘中对照这条防线一字未松（T34/T41 的前提）。"""
    from paper_trading.data import raw_feed

    res = raw_feed.sync_raw(
        data_db, ["000792"], days=5, today=date(2026, 10, 29),
        now=datetime(2026, 10, 29, 16, 0),
        fetch=lambda s, d: [mkbar("000792", date(2026, 10, 29), 22.21)],
        reference=lambda syms, d: {"000792": {}},
        intraday={"000792": {"price": 22.21, "date": "2026-10-29"}})
    assert res["intraday_enforced"] is True
    assert res["intraday_checked"] == 1


def test_flip_watch_with_mother_uses_named_range(data_db, monkeypatch):
    """逐日闸的三处窗口（参照、审计起点、审计终点）必须听同一份 start/end。"""
    from paper_trading.data import raw_feed
    from paper_trading.integration import mother as _m

    seen = {}
    monkeypatch.setattr(_m, "raw_close_map",
                        lambda codes, days=30, since=None, **kw:
                        seen.update({"since": since, "codes": list(codes)}) or {})
    monkeypatch.setattr(raw_feed, "raw_flip_watch",
                        lambda db, syms, **kw: seen.update({"watch": kw}) or {})
    raw_feed.flip_watch_with_mother(data_db, ["000792"], today=date(2026, 10, 29),
                                    start=date(2026, 10, 1), end=date(2026, 10, 15))
    assert seen["since"] == "2026-10-01"                  # 参照从指名起点取
    assert seen["watch"]["today"] == date(2026, 10, 15)   # 审计终点=指名终点，不是真今天
    assert seen["watch"]["since"] == date(2026, 10, 1)
