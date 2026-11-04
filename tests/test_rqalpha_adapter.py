"""T32 适配器测试：band 供数、状态持久化（钱类件）、会话闸、门禁、影子对账。

除 `test_bundle_*` 外**全部不需要 rqalpha/h5py**——产品 venv 不装 rqalpha，
这些用例必须在 NEC 也能跑绿。
"""
import json
import math
from datetime import date, datetime, timedelta

import pytest

from paper_trading.integration.rq_engine.bands import band_for, limit_prices, round_tick
from paper_trading.integration.rq_engine.provider import (FilePersistProvider,
                                                       SessionGate,
                                                       SessionGateError,
                                                       config_fingerprint)
from paper_trading.integration.rq_engine.runner import (build_config, check_session_gates,
                                                     session_fingerprint)
from paper_trading.integration.rq_engine.shadow import parity_diff
from paper_trading.integration.rq_engine.store import (BandMissingError, RawDayBarStore,
                                                    to_order_book_id)
from paper_trading.models import Bar


def mk(sym, d, close, *, o=None, h=None, l=None, volume=1000, amount=1e7, adjust="raw"):
    return Bar(symbol=sym, timestamp=datetime.combine(d, datetime.min.time()),
               open=o if o is not None else close,
               high=h if h is not None else round(close + 0.1, 2),
               low=l if l is not None else round(close - 0.1, 2),
               close=close, volume=volume, amount=amount, adjust=adjust)


def series(sym, closes, start=date(2026, 10, 1)):
    return [mk(sym, start + timedelta(days=i), c) for i, c in enumerate(closes)]


# ---------- band（AGENTS 合规表 #4/#5/#6 + 硬规则 2）----------
def test_band_by_board():
    assert band_for("600519") == 0.10
    assert band_for("000792") == 0.10
    assert band_for("300750") == 0.20
    assert band_for("688981") == 0.20
    assert band_for("830799") == 0.30
    assert band_for("430047") == 0.30


def test_st_does_not_downgrade_band():
    """2026-07-06 起主板 ST 与主板同为 10%（不得沿用 ST=5% 旧口径）。"""
    assert band_for("600519", is_st=True) == 0.10
    assert band_for("300750", is_st=True) == 0.20


def test_round_tick_half_up():
    assert round_tick(22.89 * 1.1) == 25.18       # 母库/事故复现基准：一字板 25.18
    assert round_tick(1300.005) == 1300.01
    up, dn = limit_prices(22.89, "002648")
    assert (up, dn) == (25.18, 20.60)


def test_limit_prices_reject_bad_prev_close():
    for bad in (0, -1, None, float("nan")):
        assert limit_prices(bad, "600519") == (math.nan, math.nan)


# ---------- DayBarStore ----------
def test_store_builds_bars_with_prev_close_and_band():
    st = RawDayBarStore({"000792": series("000792", [22.00, 22.24, 22.21])},
                        today=date(2026, 10, 3))
    bars = st.get_bars(to_order_book_id("000792"))
    assert len(bars) == 3
    assert int(bars["datetime"][0]) == 20261001000000
    assert bars["prev_close"][2] == pytest.approx(22.24)
    assert bars["limit_up"][2] == pytest.approx(round_tick(22.24 * 1.1))
    assert bars["total_turnover"][2] == pytest.approx(1e7)
    assert st.stats[to_order_book_id("000792")]["bands_ok"] == 2   # 首根无前收


def test_store_clips_to_today():
    st = RawDayBarStore({"000792": series("000792", [22.0, 22.1, 22.2])},
                        today=date(2026, 10, 2))
    assert st.get_date_range("000792.XSHE")[1] == 20261002000000


def test_store_refuses_first_day_only_series_without_band():
    with pytest.raises(BandMissingError):
        RawDayBarStore({"000792": series("000792", [22.0])}, today=date(2026, 10, 1))


def test_store_refuses_mixed_caliber_ohlc():
    """qfq 的 OHLC 拼 raw 的 close 会出现 high<close 这类不自洽。"""
    rows = series("000792", [22.00, 22.24]) + [
        mk("000792", date(2026, 10, 3), 30.00, o=22.0, h=22.3, l=21.9)]
    with pytest.raises(BandMissingError, match="OHLC 不自洽"):
        RawDayBarStore({"000792": rows}, today=date(2026, 10, 3))


def test_store_allows_missing_amount_when_band_gate_off():
    rows = [mk("000792", date(2026, 10, 1), 22.0, amount=None),
            mk("000792", date(2026, 10, 2), 22.24, amount=None)]
    st = RawDayBarStore({"000792": rows}, today=date(2026, 10, 2), require_bands=True)
    assert math.isnan(st.get_bars("000792.XSHE")["total_turnover"][-1])
    assert st.stats["000792.XSHE"]["turnover_ok"] == 0


# ---------- FilePersistProvider（钱类件）----------
def test_provider_roundtrip_is_byte_exact(tmp_path):
    p = FilePersistProvider(tmp_path / "state")
    payload = json.dumps({"total_cash": 97774.0, "non_closable": 100}).encode()
    p.store("portfolio", payload)
    assert p.load("portfolio") == payload
    assert p.keys() == ["portfolio"]
    assert not list((tmp_path / "state").glob("*.tmp"))          # 不留临时文件


def test_provider_detects_tampered_state(tmp_path):
    p = FilePersistProvider(tmp_path / "state")
    p.store("portfolio", b'{"total_cash": 1.0}')
    victim = tmp_path / "state" / "portfolio.state"
    victim.write_bytes(b'{"total_cash": 2.0}')                    # 半截写/篡改
    assert p.load("portfolio") is None
    assert "校验和不符" in p.restore_errors[-1]


def test_provider_refuses_state_without_checksum(tmp_path):
    p = FilePersistProvider(tmp_path / "state")
    (tmp_path / "state" / "executor.state").write_bytes(b"{}")
    assert p.load("executor") is None
    assert "无校验和" in p.restore_errors[-1]


def test_provider_snapshot_reports_restore_errors(tmp_path):
    p = FilePersistProvider(tmp_path / "s")
    p.store("broker", b"x")
    p.load("missing_key")
    snap = p.snapshot()
    assert snap["keys"] == ["broker"] and snap["restore_errors"] == []
    assert snap["sizes"]["broker"] == 1


# ---------- SessionGate（G-T13-5）----------
def test_session_gate_blocks_same_day_and_rollback(tmp_path):
    g = SessionGate(tmp_path)
    fp = config_fingerprint({"accounts": {"STOCK": 100000.0}})
    assert g.check(date(2026, 10, 27), fp)["allowed"]
    g.mark_done(date(2026, 10, 27), fp)
    with pytest.raises(SessionGateError, match="同日重跑"):
        g.check(date(2026, 10, 27), fp)
    with pytest.raises(SessionGateError, match="不晚于"):
        g.check(date(2026, 10, 26), fp)
    assert g.check(date(2026, 10, 28), fp)["prior"] == "2026-10-27"


def test_session_gate_force_allows_but_records(tmp_path):
    g = SessionGate(tmp_path)
    fp = "abc"
    g.mark_done(date(2026, 10, 27), fp)
    assert g.check(date(2026, 10, 27), fp, force=True)["force"] is True


def test_session_gate_blocks_fingerprint_drift(tmp_path):
    g = SessionGate(tmp_path)
    g.mark_done(date(2026, 10, 27), config_fingerprint({"init_cash": 100000.0}))
    with pytest.raises(SessionGateError, match="配置指纹漂移"):
        g.check(date(2026, 10, 28), config_fingerprint({"init_cash": 200000.0}))


def test_session_gate_rejects_corrupt_meta(tmp_path):
    (tmp_path / "session.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SessionGateError, match="解析失败"):
        SessionGate(tmp_path).check(date(2026, 10, 28), "fp")


# ---------- runner：配置与后置门禁（G-T13-6/7）----------
def _cfg(tmp_path, state_dir=""):
    # T51：费率必须显式给（现网 TradingConfig），这里统一从 config 口径取
    from paper_trading.models import TradingConfig
    from paper_trading.utils import fees as ufees

    return build_config(today="2026-10-28", data_db=str(tmp_path / "data.db"),
                        bundle="C:/bundle", symbols=["000792", "600519"],
                        state_dir=state_dir, evidence=str(tmp_path / "ev.json"),
                        fee=ufees.fee_dict(TradingConfig()))


def test_build_config_single_day_and_caliber_rules(tmp_path):
    cfg = _cfg(tmp_path, state_dir=str(tmp_path / "st"))
    assert cfg["base"]["start_date"] == cfg["base"]["end_date"] == "2026-10-28"
    assert cfg["base"]["run_type"] == "p"
    assert cfg["base"]["persist"] is True
    assert cfg["mod"]["sys_analyser"]["enabled"] is False      # G-T13-6
    assert cfg["mod"]["sys_simulation"]["matching_type"] == "current_bar"
    assert cfg["mod"]["sys_simulation"]["price_limit"] is True
    assert cfg["mod"]["ptpaper"]["enabled"] is True
    assert cfg["mod"]["ptpaper"]["require_bands"] is True      # G-T13-3


def test_build_config_without_state_dir_disables_persist(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg["base"]["persist"] is False


def test_session_fingerprint_is_sensitive_and_stable(tmp_path):
    a = session_fingerprint(_cfg(tmp_path))
    assert a == session_fingerprint(_cfg(tmp_path))
    cfg = _cfg(tmp_path)
    cfg["base"]["accounts"]["STOCK"] = 1.0
    assert session_fingerprint(cfg) != a
    cfg2 = _cfg(tmp_path)
    cfg2["mod"]["sys_simulation"]["price_limit"] = False
    assert session_fingerprint(cfg2) != a


def test_gates_catch_idle_spin(tmp_path):
    v = check_session_gates({"event_counts": {"BAR": 1}}, [], handler_calls=0)
    assert not v["ok"] and any("handle_bar" in e for e in v["errors"])


def test_gates_catch_silent_order_loss():
    """有计划下单，却既无成交也无拒单原因 = 静默失败（纪律 4 不许）。"""
    plan = [{"action": "buy", "symbol": "000792", "volume": 100}]
    v = check_session_gates({"event_counts": {"BAR": 1}, "trades": [], "rejects": []},
                            plan, handler_calls=1)
    assert not v["ok"] and any("既无成交也无拒单" in e for e in v["errors"])


def test_gates_accept_reject_with_reason():
    plan = [{"action": "buy", "symbol": "002648", "volume": 100}]
    ev = {"event_counts": {"BAR": 1, "SETTLEMENT": 1}, "trades": [],
          "rejects": [{"symbol": "002648.XSHE", "status": "REJECTED_BY_VALIDATOR",
                       "reason": "触及涨跌停价"}], "provider": {"restore_errors": []}}
    v = check_session_gates(ev, plan, handler_calls=1)
    assert v["ok"] and v["rejects"] == 1


def test_gates_flag_persist_restore_errors():
    ev = {"event_counts": {"BAR": 1}, "trades": [{"x": 1}],
          "provider": {"restore_errors": ["portfolio: 校验和不符"]}}
    v = check_session_gates(ev, [{"action": "buy", "symbol": "000792", "volume": 100}],
                            handler_calls=1)
    assert not v["ok"] and any("PersistState" in e for e in v["errors"])


def test_gates_warn_on_double_settlement():
    ev = {"event_counts": {"BAR": 1, "SETTLEMENT": 2}, "trades": [{"a": 1}],
          "rejects": [], "provider": {"restore_errors": []}}
    v = check_session_gates(ev, [{"action": "buy", "symbol": "000792", "volume": 100}],
                            handler_calls=1)
    assert v["ok"] and any("重放昨日结算" in w for w in v["warnings"])


# ---------- 影子对账（纯算术部分）----------
def test_parity_diff_rejects_the_old_transfer_fee_gap():
    """T38 裁决 5 之后，"过户费差 0.001%"不再是已知差：过户费已进引擎，
    这种形状就是费率口径漂移（佣金万八没换算掉也是同一个量级的错），必须报。

    旧版本此用例断言"gap 可接受"（T29 前提：框架无过户费）——前提被 T38 推翻，
    留着它就等于允许 fee_delta 一直不归零。
    """
    rq = {"cash": 97774.0, "fees": 5.0, "positions": {"000792.XSHE": {"quantity": 100}},
          "fills": [1], "error": None}
    legacy = {"cash": 97773.98, "fees": 5.02221,
              "positions": {"000792.XSHE": {"quantity": 100}}, "fills": [1]}
    d = parity_diff(rq, legacy)
    assert any("fee_delta" in v for v in d["violations"])
    assert not any("cash_delta" in v for v in d["violations"])   # 2 分现金差在容差内


def test_parity_diff_clean_when_fees_match():
    rq = {"cash": 97774.0, "fees": 5.02221, "positions": {"000792.XSHE": {"quantity": 100}},
          "fills": [1], "error": None}
    legacy = {"cash": 97774.0, "fees": 5.02221,
              "positions": {"000792.XSHE": {"quantity": 100}}, "fills": [1]}
    d = parity_diff(rq, legacy)
    assert d["fee_delta"] == 0.0 and d["cash_delta"] == 0.0 and not d["violations"]


def test_parity_diff_flags_position_and_fill_mismatch():
    rq = {"cash": 100.0, "fees": 10.0, "positions": {"000792.XSHE": {"quantity": 200}},
          "fills": [1, 2]}
    legacy = {"cash": 100.0, "fees": 10.0, "positions": {}, "fills": [1]}
    d = parity_diff(rq, legacy)
    assert any("持仓不一致" in v for v in d["violations"])
    assert any("成交笔数" in v for v in d["violations"])


def test_parity_diff_flags_cash_gap_beyond_tolerance():
    rq = {"cash": 1000.0, "fees": 0.0, "positions": {}, "fills": []}
    legacy = {"cash": 900.0, "fees": 0.0, "positions": {}, "fills": []}
    d = parity_diff(rq, legacy, tolerance=1.0)
    assert any("cash_delta" in v for v in d["violations"])


# ---------- 需要 rqalpha/h5py 的部分（产品 venv 会跳过）----------
def test_bundle_readers(tmp_path):
    h5py = pytest.importorskip("h5py")
    import numpy as np

    from paper_trading.integration.rq_engine.data_source import (bundle_factor_last,
                                                              bundle_price_last)

    (tmp_path / "stocks.h5").parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(tmp_path / "stocks.h5", "w") as f:
        f.create_dataset("000792.XSHE", data=np.array(
            [(20261027000000, 22.21), (20261028000000, 22.10)],
            dtype=[("datetime", "i8"), ("close", "f8")]))
    with h5py.File(tmp_path / "ex_cum_factor.h5", "w") as f:
        f.create_dataset("000792.XSHE", data=np.array(
            [(20260601000000, 1.0)], dtype=[("start_date", "i8"), ("ex_cum_factor", "f8")]))
    assert bundle_price_last(tmp_path, "000792.XSHE") == date(2026, 10, 28)
    assert bundle_factor_last(tmp_path, "000792.XSHE") == date(2026, 6, 1)
    assert bundle_price_last(tmp_path, "999999.XSHE") is None


def test_data_source_requires_rqalpha():
    pytest.importorskip("rqalpha")
    from paper_trading.integration.rq_engine.data_source import PaperDataSource  # noqa: F401


# ---------- 错误归因（rqalpha 会把真因埋进 ExceptionGroup）----------
def test_describe_error_unwraps_exception_group():
    from paper_trading.integration.rq_engine.runner import describe_error

    try:
        try:
            raise ModuleNotFoundError("No module named 'rqalpha_mod_ptpaper'")
        except ModuleNotFoundError as inner:
            raise ExceptionGroup("Mod tear down failed", [inner]) from inner
    except BaseException as exc:
        msg = describe_error(exc)
    assert "No module named 'rqalpha_mod_ptpaper'" in msg       # 真因必须露出来
    assert "Mod tear down failed" in msg


def test_describe_error_dedupes_repeats():
    from paper_trading.integration.rq_engine.runner import describe_error

    msg = describe_error(ValueError("boom"))
    assert msg == "ValueError: boom"


def test_mod_alias_registration():
    pytest.importorskip("rqalpha")
    from paper_trading.integration.rq_engine import runner as R

    R._register_mod()
    import sys

    assert "rqalpha_mod_ptpaper" in sys.modules
    assert hasattr(sys.modules["rqalpha_mod_ptpaper"], "load_mod")
