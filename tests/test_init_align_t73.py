"""T73 期初对齐的单测（不需要 rqalpha，CI 里跑）。

钉住四条 11-05 的教训：
1. `init_positions` 必须是**字符串**形态——rqalpha 的 `parse_init_positions` 对
   dict 一律返回 `[]`（实测），这是"账本持仓进不了框架"的真因；
2. 解析后必须**逐只核对**（`reconcile_init_positions`），静默少一只就停线；
3. T+1 冻结**只加不减**（`t1_freeze_plan`）：调小 `_non_closable` 等于拆掉合规闸；
4. 期初三件套只有**一条口径**（`ledger_init_state`），交易所映射不再各处自拼。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_HAS_RQALPHA = importlib.util.find_spec("rqalpha") is not None

from paper_trading.integration.rq_engine import runner        # noqa: E402
from paper_trading.integration.rq_engine.runner import (    # noqa: E402
    build_config, init_positions_arg, ledger_init_state, reconcile_init_positions,
    session_fingerprint, t1_freeze_plan)

FEE = {"commission_rate": 0.00025, "commission_min": 5.0,
       "stamp_duty_rate": 0.0005, "transfer_fee_rate": 0.00001}


def _cfg(**kw):
    base = dict(today="2026-11-05", data_db="/tmp/data.db", bundle="/tmp/bundle",
                symbols=["002192"], fee=FEE, init_cash=50000.0)
    base.update(kw)
    return build_config(**base)


# ------------------------------------------------------------------ 形态（真因）
def test_init_positions_is_string_form():
    assert init_positions_arg({}) == ""
    assert init_positions_arg(None) == ""
    assert init_positions_arg({"002192.XSHE": 100}) == "002192.XSHE:100"
    # 排序=指纹稳定（dict 插入序随 sqlite 返回序变）
    a = init_positions_arg({"600519.XSHG": 200, "002192.XSHE": 100})
    assert a == "002192.XSHE:100,600519.XSHG:200"
    assert a == init_positions_arg({"002192.XSHE": 100, "600519.XSHG": 200})


def test_init_positions_zero_skipped_garbage_loud():
    # 清仓后账本留着 total_volume=0 的行：跳过，不能把整个会话顶掉
    assert init_positions_arg({"002192.XSHE": 0}) == ""
    with pytest.raises(ValueError):
        init_positions_arg({"002192": 100})          # 缺交易所后缀=挂错合约
    with pytest.raises(ValueError):
        init_positions_arg({"002192.XSHE": -100})    # 股票账本不会做空=脏数据
    with pytest.raises(ValueError):
        init_positions_arg({"002192.XSHE": 10.5})


def test_build_config_emits_string_and_keeps_dict_for_mod():
    cfg = _cfg(init_positions={"002192.XSHE": 100, "300390.XSHE": 200})
    assert isinstance(cfg["base"]["init_positions"], str)
    assert cfg["base"]["init_positions"] == "002192.XSHE:100,300390.XSHE:200"
    # mod 复核要的是原始 dict（解析后形态就变了，对不上才知道框架吞了）
    assert cfg["mod"]["ptpaper"]["init_positions"] == {
        "002192.XSHE": 100, "300390.XSHE": 200}


def test_supply_covers_init_positions_symbols_not_only_plan():
    """store 的标的必须含期初持仓：Portfolio 取 prev_date 成本价要问它（会起不来）。"""
    cfg = _cfg(symbols=["000858"], init_positions={"002192.XSHE": 100})
    assert cfg["mod"]["ptpaper"]["symbols"] == ["000858", "002192"]


@pytest.mark.skipif(not _HAS_RQALPHA, reason="引擎 venv 才有 rqalpha")
def test_rqalpha_parse_accepts_our_form():
    """有 rqalpha 时把契约钉死：我们的字符串解析回得来，dict 解析不回来。"""
    from rqalpha.utils.config import parse_init_positions

    assert parse_init_positions(init_positions_arg(
        {"002192.XSHE": 100, "600519.XSHG": 200})) == [
        ("002192.XSHE", 100.0), ("600519.XSHG", 200.0)]
    # 这就是 11-05 的 P0：dict 进去，框架给你一个空账户，且一声不响
    assert parse_init_positions({"002192.XSHE": 100}) == []


# ------------------------------------------------------------------ 核对（哨兵）
def test_reconcile_keeps_and_drops_loud():
    exp = {"002192.XSHE": 100, "300390.XSHE": 200}
    parsed = [("002192.XSHE", 100.0)]
    out = reconcile_init_positions(exp, parsed, known=lambda ob: True)
    assert out["kept"] == [("002192.XSHE", 100)]
    assert [d["symbol"] for d in out["dropped_silently"]] == ["300390.XSHE"]
    assert out["unsupported"] == []


def test_reconcile_unsupported_is_dropped_not_an_error():
    """开源 bundle 没有北交所 .BJ：剔掉照常开会话，不算"被吞"。"""
    out = reconcile_init_positions({"920156.BJ": 100}, [("920156.BJ", 100.0)],
                                   known=lambda ob: False)
    assert out["kept"] == [] and out["dropped_silently"] == []
    assert out["unsupported"] == [{"symbol": "920156.BJ", "volume": 100}]


def test_reconcile_qty_mismatch_is_dropped():
    out = reconcile_init_positions({"002192.XSHE": 100}, [("002192.XSHE", 200.0)],
                                   known=lambda ob: True)
    assert out["kept"] == [] and len(out["dropped_silently"]) == 1


# ------------------------------------------------------------------ T+1 只加不减
def test_t1_freeze_plan_only_increases():
    plan = t1_freeze_plan({"002192.XSHE": 100},
                          {"002192.XSHE": {"quantity": 100, "non_closable": 40}})
    assert plan == [{"symbol": "002192.XSHE", "from": 40, "to": 100,
                     "quantity": 100, "changed": True}]
    # 框架已冻得更多（当天买入的 200 股）⇒ 不许减回 100：调小=解锁 T+0 卖出
    p2 = t1_freeze_plan({"002192.XSHE": 100},
                        {"002192.XSHE": {"quantity": 300, "non_closable": 200}})
    assert p2[0]["to"] == 200 and p2[0]["changed"] is False


def test_t1_freeze_plan_caps_at_quantity_and_skips_absent():
    p = t1_freeze_plan({"002192.XSHE": 500, "300390.XSHE": 200},
                       {"002192.XSHE": {"quantity": 100, "non_closable": 0}})
    assert [x["symbol"] for x in p] == ["002192.XSHE"]     # 框架没这只 ⇒ 不排
    assert p[0]["to"] == 100                               # 冻结不可能超过持仓
    assert t1_freeze_plan({"002192.XSHE": 0}, {}) == []


# ------------------------------------------------------------------ 账本口径
def _ledger(tmp_path, cash=50000.0, rows=(("002192", 100, 100), ("300390", 200, 0),
                                          ("600519", 200, 200), ("920156", 100, 100),
                                          ("000858", 0, 0))):
    from paper_trading.broker.paper_broker import PaperBroker
    from paper_trading.models import TradingConfig

    path = str(tmp_path / "acct.db")
    br = PaperBroker(db_path=path, config=TradingConfig(initial_cash=cash))
    with br._connect() as conn:                    # noqa: SLF001 - 造账本副本形状
        conn.execute("UPDATE account SET cash=?, initial_cash=? WHERE id=1",
                     (cash, cash))
        conn.execute("DELETE FROM positions")
        for sym, tot, avail in rows:
            conn.execute("INSERT INTO positions (symbol, total_volume,"
                         " available_volume, avg_cost, last_update)"
                         " VALUES (?,?,?,?,?)", (sym, tot, avail, 10.0, "x"))
    return path


def test_ledger_init_state_single_caliber(tmp_path):
    out = ledger_init_state(_ledger(tmp_path))
    assert out["cash"] == 50000.0
    # total_volume=0 的行不进期初（rqalpha 侧会被 init_positions_arg 拒掉）
    assert out["positions"] == {"002192.XSHE": 100, "300390.XSHE": 200,
                                "600519.XSHG": 200, "920156.BJ": 100}
    assert out["frozen"] == {"300390.XSHE": 200}


def test_ledger_init_state_uses_markets_mapping_not_first_char(tmp_path):
    """`920156` 必须判北交所：拼成 `920156.XSHG` 就是"框架查不到⇒这仓静默消失"。"""
    out = ledger_init_state(_ledger(tmp_path, rows=(("920156", 100, 100),)))
    assert list(out["positions"]) == ["920156.BJ"]


def test_ledger_init_state_loud_when_no_account(tmp_path):
    bad = tmp_path / "empty.db"
    import sqlite3
    sqlite3.connect(bad).execute("CREATE TABLE t(x)").connection.close()
    with pytest.raises(Exception):                 # noqa: BLE001 - 读不出就该炸
        ledger_init_state(str(bad))


# ------------------------------------------------------------------ 指纹（T74 P1）
def test_fingerprint_intraday_survives_ledger_drift():
    """第一跳成交后账本变了（现金/持仓/冻结），第二跳**必须还能续跑**。"""
    hop1 = _cfg(state_dir="/tmp/s", init_cash=50000.0, symbols=["002192"],
                init_positions={"002192.XSHE": 100}, init_frozen={})
    fp1 = session_fingerprint(hop1, intraday=True)
    hop2 = _cfg(state_dir="/tmp/s", init_cash=44990.0, symbols=["600519"],
                init_positions={"002192.XSHE": 0, "600519.XSHG": 200},
                init_frozen={"600519.XSHG": 200})
    assert session_fingerprint(hop2, intraday=True) == fp1


def test_fingerprint_day_mode_still_pins_the_baseline():
    """不是续跑（新会话）时期初量仍然进指纹：换现金基数/持仓来源就是另一本账。"""
    a = _cfg(init_cash=50000.0, init_positions={"002192.XSHE": 100})
    b = _cfg(init_cash=100000.0, init_positions={"002192.XSHE": 100})
    assert session_fingerprint(a) != session_fingerprint(b)
    c = _cfg(init_cash=50000.0, init_positions={"002192.XSHE": 200})
    assert session_fingerprint(a) != session_fingerprint(c)


def test_fingerprint_semantic_knob_still_blocks_resume():
    """日内豁免只豁免"账本派生量"：改费率这种真语义漂移照样要拒。"""
    a = _cfg(state_dir="/tmp/s", fee=FEE)
    b = _cfg(state_dir="/tmp/s", fee=dict(FEE, commission_rate=0.0003))
    assert session_fingerprint(a, intraday=True) != session_fingerprint(
        b, intraday=True)
