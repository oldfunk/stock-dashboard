"""T38 测试：费用口径对齐（过户费 overlay + 现网费率进会话指纹）与影子留痕闸。

分两层：
- **不需要 rqalpha** 的（费率换算、build_config/指纹、门禁、op_log 白名单、
  容差、影子侧费率透传）——NEC 生产 venv 也能跑；
- **需要 rqalpha** 的（overlay 真包住框架 decider）——`importorskip`，
  在装了引擎的 venv 里跑（开发机 refactor-spike / T39 的 NEC 引擎 venv）。
"""
import types as pytypes
from datetime import datetime

import pytest

from paper_trading.integration.rq_engine import costs, shadow
from paper_trading.integration.rq_engine.runner import (build_config, check_session_gates,
                                                     session_fingerprint)
from paper_trading.models import TradingConfig
from paper_trading.utils import fees as _fees


def _fee(**over):
    """现网费率（来自 TradingConfig）+ 指定覆盖项。

    T51 之后 `fee` 必须四项齐备：只给一项就报错——"半份费率"混着默认值用，
    正是这次要根除的第二真相源。
    """
    return {**_fees.fee_dict(TradingConfig()), **over}


def _cfg(**kw):
    base = dict(today="2026-10-27", data_db="data.db", bundle="bundle",
                symbols=["000858"])
    base.update(kw)
    return build_config(**base)


# ---------- ① 费率换算（纯函数）----------
def test_stock_cost_mod_config_matches_production_rates():
    # 费率从 config 侧显式传入（T51：costs.py 不再自带数字）
    c = costs.stock_cost_mod_config(**{k: v for k, v in _fee().items()
                                       if k != "transfer_fee_rate"})
    # 万 2.5 = 万八 × 0.3125；印花税千 0.5 = 千 0.5 × 1；最低佣金 5 元
    assert c["stock_commission_multiplier"] == pytest.approx(0.00025 / 0.0008)
    assert c["stock_commission_multiplier"] == pytest.approx(0.3125)
    assert c["stock_min_commission"] == 5.0
    assert c["tax_multiplier"] == pytest.approx(1.0)
    assert c["pit_tax"] is False          # 现网印花税固定，不看历史时点


def test_stock_cost_mod_config_requires_every_rate():
    """不给费率就 TypeError：兜底数字是第二真相源（纪律 7）。"""
    with pytest.raises(TypeError):
        costs.stock_cost_mod_config()


def test_zero_rates_map_to_zero_multipliers():
    c = costs.stock_cost_mod_config(commission_rate=0.0, stamp_duty_rate=0.0,
                                    commission_min=0.0)
    assert c["stock_commission_multiplier"] == 0.0
    assert c["tax_multiplier"] == 0.0
    assert c["stock_min_commission"] == 0.0


# ---------- ② build_config / 会话指纹 ----------
def test_build_config_pins_rates_instead_of_framework_defaults():
    cfg = _cfg(fee=_fee())
    tc = cfg["mod"]["sys_transaction_cost"]
    assert tc["stock_commission_multiplier"] != 1.0, "框架默认万八，配置里必须换算掉"
    assert cfg["mod"]["ptpaper"]["transfer_fee_rate"] == TradingConfig().transfer_fee_rate


def test_build_config_refuses_missing_or_partial_fee():
    """缺 fee 或只给一项 ⇒ ValueError（旧行为是"不传就用抄来的现网默认值"）。"""
    with pytest.raises(ValueError, match="费率来源"):
        _cfg()
    with pytest.raises(ValueError, match="缺"):
        _cfg(fee={"commission_rate": 0.0003})
    with pytest.raises(ValueError, match="显式给出"):
        _cfg(fee=None)


def test_build_config_accepts_custom_fee_dict():
    cfg = _cfg(fee=_fee(commission_rate=0.0001, commission_min=3.0,
                        stamp_duty_rate=0.001, transfer_fee_rate=0.00002))
    assert cfg["mod"]["sys_transaction_cost"]["stock_commission_multiplier"] \
        == pytest.approx(0.0001 / 0.0008)
    assert cfg["mod"]["sys_transaction_cost"]["tax_multiplier"] == pytest.approx(2.0)
    assert cfg["mod"]["sys_transaction_cost"]["stock_min_commission"] == 3.0
    assert cfg["mod"]["ptpaper"]["transfer_fee_rate"] == 0.00002
    # transfer_fee_rate 不属于框架的四个键，不能被混进 sys_transaction_cost
    assert "transfer_fee_rate" not in cfg["mod"]["sys_transaction_cost"]


def test_fee_change_moves_session_fingerprint():
    """G-T13-5：费率是账本语义，改了还想续跑昨天的状态 = 两本账混着算。"""
    fp_default = session_fingerprint(_cfg(fee=_fee()))
    fp_other = session_fingerprint(_cfg(fee=_fee()))
    fp_fee = session_fingerprint(_cfg(fee=_fee(commission_rate=0.0003)))
    fp_transfer = session_fingerprint(_cfg(fee=_fee(transfer_fee_rate=0.00003)))
    assert fp_default == fp_other
    assert fp_fee != fp_default
    assert fp_transfer != fp_default


# ---------- ③ 门禁：配了过户费率就必须装上 ----------
def _ev(**cost):
    return {"event_counts": {"SETTLEMENT": 1, "BAR": 1}, "trades": [], "rejects": [],
            "cost": {"wrapped": [], "transfer_fee_rate": 0.0, **cost}}


def test_gate_fails_loud_when_transfer_fee_not_installed():
    res = check_session_gates(_ev(), [{"action": "buy", "symbol": "000858", "volume": 100}],
                              handler_calls=1, expect_transfer_fee=True)
    assert not res["ok"]
    assert any("TransferFeeNotInstalled" in e for e in res["errors"])


def test_gate_passes_when_overlay_wrapped():
    ev = _ev(wrapped=["CS"])
    ev["trades"] = [{"symbol": "000858.XSHE", "cost": 26.0}]
    res = check_session_gates(ev, [{"action": "buy", "symbol": "000858", "volume": 100}],
                              handler_calls=1, expect_transfer_fee=True)
    assert res["ok"], res["errors"]


def test_gate_ignores_when_rate_zero():
    res = check_session_gates(_ev(), [], handler_calls=1, expect_transfer_fee=False)
    assert res["ok"]


# ---------- ④ 裁决 4：op_log 白名单 ----------
class FakeBroker:
    def __init__(self):
        self.rows = []

    def log_operation(self, action, params=None, ok=True, result=None,
                      cash_after=None, total_value_after=None):
        self.rows.append({"action": action, "ok": ok, "params": params, "result": result})


def _res():
    return {"today": "2026-10-27", "ok": True,
            "diff": {"position_delta": {"000858": {"delta": 0}}, "violations": [],
                     "cash_delta": 0.0, "fee_delta": 0.0, "fill_delta": 0},
            "rqalpha": {"cash": 100.0}, "legacy": {"cash": 100.0}}


def test_record_to_writes_parity_diff_by_default():
    b = FakeBroker()
    shadow.record_to(b, _res())
    assert [r["action"] for r in b.rows] == ["parity:diff"]
    assert b.rows[0]["result"]["fee_delta"] == 0.0


@pytest.mark.parametrize("bad", ["buy", "parity:diff:extra", "", "data:migrate"])
def test_record_to_rejects_any_other_action(bad):
    """白名单锁死：影子这条路径只允许 parity:diff 进生产 op_log。"""
    b = FakeBroker()
    with pytest.raises(ValueError):
        shadow.record_to(b, _res(), action=bad)
    assert b.rows == []


def test_whitelist_contains_exactly_one_action():
    assert shadow.OP_LOG_ACTIONS == frozenset({"parity:diff"})


# ---------- ⑤ 费用容差：验收信号是"归零"，不是"差 1% 也行" ----------
def test_fee_delta_small_passes_large_fails():
    ok = shadow.parity_diff({"cash": 1.0, "fees": 100.0}, {"cash": 1.0, "fees": 100.005})
    assert not [v for v in ok["violations"] if "fee_delta" in v]
    bad = shadow.parity_diff({"cash": 1.0, "fees": 101.0}, {"cash": 1.0, "fees": 100.0})
    assert any("fee_delta" in v for v in bad["violations"])


def test_fee_tolerance_is_absolute_not_relative():
    """旧实现是相对 1%：费用基数大时 500 元的差也能"过"——那正是佣金口径漂移的形状。"""
    big = shadow.parity_diff({"cash": 1.0, "fees": 51000.0}, {"cash": 1.0, "fees": 50000.0})
    assert any("fee_delta" in v for v in big["violations"])


# ---------- ⑥ 影子两侧费率同源 ----------
def test_legacy_side_uses_passed_fee_rates(tmp_path):
    from paper_trading.data.db_manager import DataDBManager
    from paper_trading.models import Bar

    db = DataDBManager(str(tmp_path / "data.db"))
    bars = [Bar(symbol="000858", timestamp=datetime(2026, 10, i, 15, 0),
                open=50.0, high=50.0, low=50.0, close=50.0,
                volume=10000, amount=500000.0, adjust="raw") for i in (1, 2, 3)]
    assert db.upsert_raw_bars(bars, source="test") == 3
    bars_by_symbol = {"000858": db.get_raw_bars("000858", limit=30)}

    zero = shadow._legacy_side(today="2026-10-03", bars_by_symbol=bars_by_symbol,
                               plan=[{"action": "buy", "symbol": "000858", "volume": 2000}],
                               shadow_db=tmp_path / "a.db", init_cash=1_000_000.0,
                               fee={"commission_rate": 0.0, "commission_min": 0.0,
                                    "stamp_duty_rate": 0.0, "transfer_fee_rate": 0.0})
    assert zero["fees"] == 0.0

    only_transfer = shadow._legacy_side(
        today="2026-10-03", bars_by_symbol=bars_by_symbol,
        plan=[{"action": "buy", "symbol": "000858", "volume": 2000}],
        shadow_db=tmp_path / "b.db", init_cash=1_000_000.0,
        fee={"commission_rate": 0.0, "commission_min": 0.0, "stamp_duty_rate": 0.0})
    # 2000 股 × 50 元 = 10 万，过户费 0.001% = 1.0 元
    assert only_transfer["fees"] == pytest.approx(1.0)


# ---------- ⑦ 需要 rqalpha 的 overlay 真身 ----------
class FakeEnv:
    def __init__(self, store=None):
        self._transaction_cost_deciders = store or {}

    def set_transaction_cost_decider(self, instrument_type, decider, market=None):
        self._transaction_cost_deciders[(instrument_type, market)] = decider


class InnerDecider:
    """替身：佣金 = 成交额 × 0.0008 × 倍率（框架的原样），印花税只卖。"""

    def __init__(self, multiplier=1.0, min_commission=5.0):
        self.multiplier = multiplier
        self.min_commission = min_commission
        self.calls = 0

    def calc(self, args):
        from rqalpha.interface import TransactionCost

        self.calls += 1
        amount = args.price * args.quantity
        return TransactionCost(commission=max(amount * 0.0008 * self.multiplier,
                                              self.min_commission),
                               tax=amount * 0.0005 if str(args.side) == "SIDE.SELL" else 0,
                               other_fees=0)

    def batch_estimate(self, delta_quantities, prices):
        return delta_quantities.abs() * prices * 0.0008 * self.multiplier


def test_overlay_adds_transfer_fee_to_other_fees():
    pytest.importorskip("rqalpha")
    from rqalpha.const import INSTRUMENT_TYPE, SIDE

    inner = InnerDecider(multiplier=0.3125)
    env = FakeEnv({(INSTRUMENT_TYPE.CS, None): inner})
    wrapped = costs.install_transfer_fee(env, TradingConfig().transfer_fee_rate)
    assert wrapped == ["CS"]                      # INSTRUMENT_TYPE.CS 的 value（const.py:148）

    dec = env._transaction_cost_deciders[(INSTRUMENT_TYPE.CS, None)]   # 存的是对象本身
    args = pytypes.SimpleNamespace(price=50.0, quantity=2000, side=SIDE.BUY,
                                   instrument=None, position_effect=None, order_id=None)
    cost = dec.calc(args)
    assert cost.commission == pytest.approx(100000 * 0.00025)      # 佣金透传，未改写
    assert cost.other_fees == pytest.approx(1.0)                   # 过户费 = 10 万 × 0.001%
    assert cost.total == pytest.approx(25.0 + 1.0)
    assert inner.calls == 1, "必须委托给原 decider，而不是自己重算佣金"


def test_overlay_is_idempotent():
    """重复安装不能套两层——套了就等于收两遍过户费。"""
    pytest.importorskip("rqalpha")
    from rqalpha.const import INSTRUMENT_TYPE, SIDE

    env = FakeEnv({(INSTRUMENT_TYPE.CS, None): InnerDecider()})
    assert costs.install_transfer_fee(env, 0.00001) == ["CS"]
    assert costs.install_transfer_fee(env, 0.00001) == [], "第二次不该再包一层"

    dec = env._transaction_cost_deciders[(INSTRUMENT_TYPE.CS, None)]   # 存的是对象本身
    args = pytypes.SimpleNamespace(price=50.0, quantity=2000, side=SIDE.BUY,
                                   instrument=None, position_effect=None, order_id=None)
    assert dec.calc(args).other_fees == pytest.approx(1.0)


def test_overlay_skips_etf_fund_and_lof():
    """过户费只对股票成立：ETF/公募/LOF 不该被包（真实规则里基金没有这一项）。

    框架的注册循环本身就跳过了 ETF 与 PUBLIC_FUND（`mod.py:134-140`），
    这里锁的是"我们不会把它们捞回来"。
    """
    pytest.importorskip("rqalpha")
    from rqalpha.const import INSTRUMENT_TYPE, MARKET

    etf, lof, fund = InnerDecider(), InnerDecider(), InnerDecider()
    env = FakeEnv({(INSTRUMENT_TYPE.ETF, MARKET.CN): etf,
                   (INSTRUMENT_TYPE.LOF, MARKET.CN): lof,
                   (INSTRUMENT_TYPE.PUBLIC_FUND, MARKET.CN): fund})
    Overlay = costs.make_overlay_class()
    assert costs.install_transfer_fee(env, 0.00001) == []
    assert list(env._transaction_cost_deciders.values()) == [etf, lof, fund], \
        "非 CS 合约必须原样留给框架，不被 overlay 替换"
    assert not any(isinstance(d, Overlay) for d in env._transaction_cost_deciders.values())


def test_overlay_batch_estimate_delegates_and_adds():
    pytest.importorskip("rqalpha")
    import pandas as pd

    from rqalpha.const import INSTRUMENT_TYPE

    env = FakeEnv({(INSTRUMENT_TYPE.CS, None): InnerDecider(multiplier=0.3125)})
    costs.install_transfer_fee(env, 0.00001)
    dec = env._transaction_cost_deciders[(INSTRUMENT_TYPE.CS, None)]   # 存的是对象本身
    est = dec.batch_estimate(pd.Series({"600036.SH": -2000}), pd.Series({"600036.SH": 50.0}))
    assert float(est.iloc[0]) == pytest.approx(100000 * 0.00025 + 1.0)


def test_overlay_stats_shape():
    pytest.importorskip("rqalpha")
    from rqalpha.const import INSTRUMENT_TYPE, SIDE

    env = FakeEnv({(INSTRUMENT_TYPE.CS, None): InnerDecider()})
    costs.install_transfer_fee(env, 0.00001)
    dec = env._transaction_cost_deciders[(INSTRUMENT_TYPE.CS, None)]   # 存的是对象本身
    dec.calc(pytypes.SimpleNamespace(price=10.0, quantity=100, side=SIDE.BUY,
                                     instrument=None, position_effect=None, order_id=None))
    st = costs.overlay_stats(env)
    assert st == [{"instrument_type": "CS", "rate": 1e-05, "applied": 1,
                   "added_total": 0.01}]


# ---------- ⑧ 过户费公式与旧引擎一致 ----------
def test_transfer_fee_amount_matches_legacy_formula():
    """过户费公式与旧线 `utils.fees` 同一把尺子（T38 原意，T51 起费率不再自带）。"""
    cfg = TradingConfig()
    assert costs.transfer_fee_amount(500000.0, cfg.transfer_fee_rate)         == pytest.approx(500000.0 * cfg.transfer_fee_rate)
    # 旧线侧同一个数：broker 的过户费 = 成交额 × 同一费率
    assert _fees.buy_cost(cfg, 500000.0)[1] == pytest.approx(
        costs.transfer_fee_amount(500000.0, cfg.transfer_fee_rate))
