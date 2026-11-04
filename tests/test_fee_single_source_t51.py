"""T51 费率口径唯一（纪律 7）：反射测试 + 行为测试。

两件不同的事，别混：
- **反射测试**（§1）扫源码，保证"费率数字与公式只有一份定义"这件事**不会被以后
  顺手抄一遍**破坏——纪律 7 破的时候都是"有人又写了一遍，但没人报错"；
- **行为测试**（§2-§4）证明 config.yaml 真的驱动四条路（broker / 回测 FeeModel /
  rqalpha 引擎配置 / m3 verify 重算），并钉住 T51 撞破的那个现网事实：
  三处"各自 new"读的是 `load_config(...)["trading"]`——**那个键根本不存在**，
  于是回测与面板一直在吃硬编码兜底，改 config 什么都不动。
"""
import ast
import dataclasses
import pathlib

import pytest

from paper_trading.engine.backtest import FeeModel
from paper_trading.models import TradingConfig
from paper_trading.utils import fees as ufees
from paper_trading.utils.config import load_config, trading_config_from_dicts

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "paper_trading"
#: 唯一允许出现费率数字的地方
ALLOWED_FILE = "models/types.py"
ODD = {"commission_rate": 0.001, "commission_min": 8.0,
       "stamp_duty_rate": 0.002, "transfer_fee_rate": 0.00003}


# ============================================================ ① 反射测试
def _fee_literal_hits(path: pathlib.Path) -> list[str]:
    """找出"把费率数字绑到费率键名上"的写法（三种：赋值 / 形参默认 / 关键字调用 / dict.get 兜底）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    label = (path.relative_to(ROOT).as_posix() if ROOT in path.parents else path.name)

    def where(node) -> str:
        return f"{label}:{getattr(node, 'lineno', '?')}"

    def is_num(node):
        return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                # 大小写都抓：`COMMISSION_MIN = 5.0`（costs.py 原来的写法）与
                # `commission_min = 5.0` 是同一种病——第二真相源不分命名风格
                if isinstance(t, ast.Name) and t.id.lower() in ufees.FEE_KEYS                         and is_num(node.value):
                    hits.append(f"{where(node)} 赋值 {t.id}=<数字>")
        if isinstance(node, ast.arguments):
            for arg, default in zip(reversed(node.args), reversed(node.defaults or [])):
                if arg.arg.lower() in ufees.FEE_KEYS and is_num(default):
                    hits.append(f"{where(default)} 形参 {arg.arg} 自带默认数字")
        if isinstance(node, ast.Call):
            for kw in node.keywords or []:
                if kw.arg and kw.arg.lower() in ufees.FEE_KEYS and is_num(kw.value):
                    hits.append(f"{where(kw.value)} 调用里硬写 {kw.arg}=<数字>")
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                    and len(node.args) == 2 and is_num(node.args[0]) is False \
                    and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value in ufees.FEE_KEYS and is_num(node.args[1]):
                hits.append(f"{where(node)} .get(\"{node.args[0].value}\", <兜底数字>)")
    return hits


def test_fee_rates_are_defined_exactly_once_in_the_tree():
    """四张费率数字只许出现在 `models/types.py`（TradingConfig 的字段默认）。"""
    offenders = []
    for py in sorted(PKG.rglob("*.py")):
        if py.relative_to(PKG).as_posix() == ALLOWED_FILE:
            continue
        offenders += _fee_literal_hits(py)
    assert offenders == [], "费率第二真相源：\n" + "\n".join(offenders)


def test_fee_rates_are_defined_exactly_once_in_the_tree():
    """四张费率数字只许出现在 `models/types.py`（TradingConfig 的字段默认）。"""
    offenders = []
    for py in sorted(PKG.rglob("*.py")):
        if py.relative_to(PKG).as_posix() == ALLOWED_FILE:
            continue
        offenders += _fee_literal_hits(py)
    assert offenders == [], "费率第二真相源：\n" + "\n".join(offenders)


def test_scanner_actually_bites(tmp_path):
    """反射测试的**正控制**：扫描器必须能抓到"又抄一遍"的四种写法，
    否则它就是一条永远绿的空断言（T43 的教训：静态启发式给的结论要能自证）。"""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "COMMISSION_MIN = 5.0\n"
        "def f(commission_rate=0.00025):\n"
        "    return d.get('stamp_duty_rate', 0.0005)\n",
        encoding="utf-8")
    hits = _fee_literal_hits(probe)
    assert len(hits) == 3, hits          # 赋值 / 形参默认 / .get 兜底
    probe.write_text("FeeModel(transfer_fee_rate=0.00001)\n", encoding="utf-8")
    assert len(_fee_literal_hits(probe)) == 1


def test_fee_formula_is_defined_exactly_once():
    """费用公式只许有 `utils/fees.py` 一处：别处不许再写 `max(额×率, 地板)`。

    之前有三份（broker、FeeModel、m3_verify 重算）——检查用的尺子和记账用的尺子
    分开写，早晚一天"红"的是公式漂移而不是钱错。
    """
    offenders = []
    for py in sorted(PKG.rglob("*.py")):
        rel = py.relative_to(PKG).as_posix()
        if rel == "utils/fees.py":
            continue
        offenders += _commission_formula_hits(py)
    assert offenders == [], f"佣金公式被抄到别处了：\n" + "\n".join(offenders)
    # 正控制：同一份扫描器必须认得"抄了一遍"的样子
    assert _commission_formula_hits_text(
        "def buy(a, cfg):\n    return max(a * cfg.commission_rate, cfg.commission_min)\n")


def _commission_formula_hits_text(text: str) -> list[str]:
    tree = ast.parse(text)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "max":
            seg = ast.get_source_segment(text, node) or ""
            if "commission_rate" in seg and "commission_min" in seg:
                hits.append(f"{getattr(node, 'lineno', '?')}: {seg}")
    return hits


def _commission_formula_hits(path: pathlib.Path) -> list[str]:
    label = (path.relative_to(ROOT).as_posix() if ROOT in path.parents else path.name)
    return [f"{label}:{h}" for h in _commission_formula_hits_text(
        path.read_text(encoding="utf-8"))]


def test_costs_module_owns_no_production_rate():
    """`costs.py` 只留框架基准率（除数），不留现网率。"""
    from paper_trading.integration.rq_engine import costs

    for name in ("COMMISSION_RATE", "COMMISSION_MIN", "STAMP_DUTY_RATE", "TRANSFER_FEE_RATE"):
        assert not hasattr(costs, name), f"costs.py 又长出第二真相源：{name}"
    assert costs.RQ_STOCK_COMMISSION_RATE == 0.0008     # 框架基准率：是除数，不是现网口径


# ============================================================ ② config.yaml 真驱动
def test_load_config_carries_yaml_rates_and_dataclass_fallback(tmp_path):
    """yaml 写了就用 yaml；没写就用 TradingConfig 的默认——**不再有三处兜底数字**。"""
    p = tmp_path / "config.yaml"
    p.write_text("trading:\n  commission_rate: 0.001\n  commission_min: 8.0\n"
                 "  stamp_duty_rate: 0.002\n  transfer_fee_rate: 0.00003\n", encoding="utf-8")
    cfg = load_config(p)["trading_config"]
    assert {k: getattr(cfg, k) for k in ufees.FEE_KEYS} == ODD

    p2 = tmp_path / "empty.yaml"
    p2.write_text("strategy:\n  active: general\n", encoding="utf-8")
    default_cfg = load_config(p2)["trading_config"]
    assert {k: getattr(default_cfg, k) for k in ufees.FEE_KEYS} == \
        {k: getattr(TradingConfig(), k) for k in ufees.FEE_KEYS}
    # 兜底来自数据类而不是抄写：两者必须逐字段相同
    assert dataclasses.asdict(default_cfg) == dataclasses.asdict(TradingConfig())


def test_unknown_trading_key_is_loud_not_silent(tmp_path, caplog):
    """键名写错（`commission-rate`）不许静默当成"没配"——那是"改了没生效"的另一种形态。"""
    p = tmp_path / "config.yaml"
    p.write_text("trading:\n  commission-rate: 0.001\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        load_config(p)
    assert "commission-rate" in caplog.text


def test_repo_config_yaml_agrees_with_code_default():
    """现网 config.yaml 与代码默认值今天没有分叉（有分叉就该由 config 说话，测试会红）。"""
    cfg = load_config(ROOT / "config.yaml")["trading_config"]
    assert {k: getattr(cfg, k) for k in ufees.FEE_KEYS} == \
        {k: getattr(TradingConfig(), k) for k in ufees.FEE_KEYS}


def test_backtest_and_dashboard_follow_config(tmp_path):
    """派发点名的那条：改 config 费率，**回测与面板必须跟着变**（T51 前不跟）。"""
    p = tmp_path / "config.yaml"
    p.write_text("trading:\n" + "".join(f"  {k}: {v}\n" for k, v in ODD.items()),
                 encoding="utf-8")
    fee = FeeModel.from_trading_config(load_config(p)["trading_config"])
    assert (fee.commission_rate, fee.transfer_fee_rate) == (0.001, 0.00003)
    assert fee.buy_cost(100_000.0) == pytest.approx((100.0, 3.0))     # 不再是万 2.5 的 25 元

    dash = pytest.importorskip("paper_trading.dashboard")
    fm = dash._fee_of(str(p))
    assert {k: getattr(fm, k) for k in ufees.FEE_KEYS} == ODD


# ============================================================ ③ 四条路同一把尺子
@pytest.mark.parametrize("amount", [5_000.0, 37_580.0, 500_000.0, 1_234_567.0])
def test_broker_backtest_and_fees_module_agree(amount):
    cfg = TradingConfig(**ODD)
    from paper_trading.broker.paper_broker import PaperBroker

    broker = PaperBroker.__new__(PaperBroker)          # 只测公式，不建库
    broker.config = cfg
    assert broker._calc_buy_cost(amount) == ufees.buy_cost(cfg, amount)
    assert broker._calc_sell_cost(amount) == ufees.sell_cost(cfg, amount)
    assert FeeModel.from_trading_config(cfg).buy_cost(amount) == ufees.buy_cost(cfg, amount)
    assert FeeModel.from_trading_config(cfg).sell_cost(amount) == ufees.sell_cost(cfg, amount)


def test_m3_verify_uses_the_same_formula(tmp_path):
    """`m3 verify` 的 fees 项必须与记账同一把尺子：按 config 费率写的成交要判绿。"""
    import sqlite3
    from datetime import date

    from paper_trading.broker.paper_broker import PaperBroker
    from paper_trading.utils import m3_verify as mv

    # 业务日取真实今天：`apply_external_fill` 写的是墙钟时间戳，硬编码日期过午夜就查不到
    today = date.today().isoformat()
    cfg = TradingConfig(**ODD)
    broker = PaperBroker(db_path=str(tmp_path / "acct.db"), config=cfg)
    broker.apply_external_fill(symbol="000651", direction=1, volume=1000, price=37.58,
                               commission=ufees.buy_cost(cfg, 37_580.0)[0],
                               transfer_fee=ufees.buy_cost(cfg, 37_580.0)[1])
    with sqlite3.connect(str(broker.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        r = mv.check_fees(conn, cfg, today)
    assert r["status"] == "green", r["detail"]

    # 把成交费用改成"另一个费率"算的 ⇒ 必须红（证明它真的在按 config 校验）
    with sqlite3.connect(str(broker.db_path)) as conn:
        conn.execute("UPDATE fills SET commission = 99.0")
        conn.commit()
        conn.row_factory = sqlite3.Row
        r2 = mv.check_fees(conn, cfg, today)
    assert r2["status"] == "red"


# ============================================================ ④ 兜底路径全部关闭
def test_engine_side_refuses_missing_or_partial_rates():
    from paper_trading.integration.rq_engine import costs
    from paper_trading.integration.rq_engine.runner import build_config
    from paper_trading.integration.rq_engine.shadow import shadow_day

    with pytest.raises(TypeError):
        costs.stock_cost_mod_config()
    with pytest.raises(ValueError, match="显式给出"):
        build_config(today="2026-10-29", data_db="d.db", bundle="b", symbols=["000651"])
    with pytest.raises(ValueError, match="缺"):
        build_config(today="2026-10-29", data_db="d.db", bundle="b", symbols=["000651"],
                     fee={"commission_rate": 0.001})
    with pytest.raises(ValueError, match="显式给出"):
        shadow_day(today="2026-10-29", data_db="d.db", bundle="b", symbols=["000651"],
                   plan=[], shadow_dir="sd")


def test_engine_accepts_trading_config_object_directly():
    """CLI 不再手搓 dict：直接把现网 TradingConfig 交给引擎侧（搓错过一次键名）。"""
    from paper_trading.integration.rq_engine.runner import build_config

    cfg = build_config(today="2026-10-29", data_db="d.db", bundle="b",
                       symbols=["000651"], fee=TradingConfig(**ODD))
    tc = cfg["mod"]["sys_transaction_cost"]
    assert tc["stock_commission_multiplier"] == pytest.approx(0.001 / 0.0008)
    assert tc["stock_min_commission"] == 8.0
    assert tc["tax_multiplier"] == pytest.approx(0.002 / 0.0005)
    assert cfg["mod"]["ptpaper"]["transfer_fee_rate"] == 0.00003
    assert "transfer_fee_rate" not in tc


def test_as_fee_dict_rejects_object_missing_rates():
    class NoRates:
        pass

    with pytest.raises(AttributeError, match="费率"):
        ufees.fee_dict(NoRates())
    with pytest.raises(ValueError, match="显式给出"):
        ufees.as_fee_dict(None)
    with pytest.raises(ValueError, match="缺一不可"):
        ufees.as_fee_dict({"commission_rate": 0.001, "commission_min": 8.0})


def test_trading_config_from_dicts_is_the_only_constructor():
    """`load_config` 内部的构造点可单独调用，且空输入 ⇒ 与 TradingConfig() 逐字段相同。"""
    assert dataclasses.asdict(trading_config_from_dicts({}, {})) == \
        dataclasses.asdict(TradingConfig())
    cfg = trading_config_from_dicts({"holidays": ["2026-10-01"]}, {"initial_cash": 123.0})
    assert cfg.holidays == ("2026-10-01",) and cfg.initial_cash == 123.0
