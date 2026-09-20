"""M4a Paper Trading Engine 单测"""
import json

import pytest

from src.models import database as db_mod
from src.models.database import init_database
from src.paper.engine import (
    parse_trade_signal, generate_signals, execute_signals,
    run_paper_trading, _get_latest_close,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    init_database()
    return db_path


def _make_stock(code="600519", signal="BUY", confidence="高",
                buy_zone="100-150", **overrides):
    """构造 screening_result 行"""
    trade = {
        "signal": signal,
        "confidence": confidence,
        "buy_zone": buy_zone,
        "target_price": "200",
        "stop_loss": "跌破 90",
        "take_profit": "涨到 180",
    }
    return {
        "stock_code": code,
        "code": code,
        "name": "测试股",
        "score": 85,
        "ai_trade_strategy": json.dumps(trade, ensure_ascii=False),
        **overrides,
    }


# ---- parse_trade_signal ----

def test_parse_buy_signal():
    stock = _make_stock()
    sig = parse_trade_signal(stock)
    assert sig is not None
    assert sig["code"] == "600519"
    assert sig["signal"] == "BUY"
    assert sig["confidence"] == "高"
    assert sig["buy_zone"] == (100.0, 150.0)
    assert sig["target_price"] == 200.0


def test_parse_sell_signal():
    stock = _make_stock(signal="AVOID")
    sig = parse_trade_signal(stock)
    assert sig["signal"] == "AVOID"


def test_parse_hold_returns_none():
    stock = _make_stock(signal="HOLD")
    sig = parse_trade_signal(stock)
    assert sig is None


def test_parse_no_strategy_returns_none():
    sig = parse_trade_signal({"stock_code": "600519"})
    assert sig is None


def test_parse_invalid_json_returns_none():
    stock = _make_stock()
    stock["ai_trade_strategy"] = "not-json"
    sig = parse_trade_signal(stock)
    assert sig is None


def test_parse_single_price_buy_zone():
    stock = _make_stock(buy_zone="100")
    sig = parse_trade_signal(stock)
    assert sig["buy_zone"] == (100.0, 100.0)


def test_parse_tilde_buy_zone():
    stock = _make_stock(buy_zone="100~150")
    sig = parse_trade_signal(stock)
    assert sig["buy_zone"] == (100.0, 150.0)


# ---- generate_signals ----

def test_generate_signals_batch():
    stocks = [_make_stock("600519"), _make_stock("000001", signal="AVOID"),
              _make_stock("000002", signal="HOLD")]
    sigs = generate_signals(stocks)
    assert len(sigs) == 2  # HOLD excluded
    assert sigs[0]["code"] == "600519"
    assert sigs[1]["code"] == "000001"


def test_generate_signals_empty():
    assert generate_signals([]) == []


# ---- execute_signals ----

def test_execute_buy_no_market_data(tmp_db, monkeypatch):
    """无行情数据时跳过"""
    monkeypatch.setattr("src.paper.engine._get_current_price", lambda code: None)
    broker = PaperBroker()
    sig = {"code": "600519", "signal": "BUY", "confidence": "高",
           "buy_zone": None, "name": "测试"}
    results = execute_signals([sig], broker)
    assert results[0]["action"] == "skip"
    assert "无行情" in results[0]["reason"]


def test_execute_buy_outside_zone(tmp_db, monkeypatch):
    """现价不在买入区时跳过"""
    monkeypatch.setattr("src.paper.engine._get_current_price", lambda code: 200.0)
    broker = PaperBroker()
    sig = {"code": "600519", "signal": "BUY", "confidence": "高",
           "buy_zone": (100.0, 150.0), "name": "测试"}
    results = execute_signals([sig], broker)
    assert results[0]["action"] == "skip"
    assert "不在买入区" in results[0]["reason"]


def test_execute_buy_success(tmp_db, monkeypatch):
    """买入成功"""
    monkeypatch.setattr("src.paper.engine._get_current_price", lambda code: 100.0)
    broker = PaperBroker()
    broker._cfg = {"max_position_pct": 40, "slippage_pct": 0.1,
                   "commission_rate": 0.00025, "commission_min": 5.0,
                   "stamp_tax_rate": 0.005, "transfer_fee_rate": 0.00001,
                   "max_total_position_pct": 80, "force_drawdown_pct": -15}
    sig = {"code": "600519", "signal": "BUY", "confidence": "高",
           "buy_zone": None, "name": "测试"}
    results = execute_signals([sig], broker)
    assert results[0]["action"] == "filled"
    assert results[0]["fill_price"] > 0
    pos = broker._positions.get("600519")
    assert pos is not None
    assert pos["volume"] > 0


def test_execute_sell_no_position(tmp_db, monkeypatch):
    """AVOID 但无持仓时跳过"""
    broker = PaperBroker()
    sig = {"code": "600519", "signal": "AVOID", "confidence": "中",
           "buy_zone": None, "name": "测试"}
    results = execute_signals([sig], broker)
    assert results[0]["action"] == "skip"
    assert "无可用持仓" in results[0]["reason"]


def test_execute_sell_with_position(tmp_db, monkeypatch):
    """AVOID 卖出可用持仓"""
    monkeypatch.setattr("src.paper.engine._get_current_price", lambda code: 100.0)
    broker = PaperBroker()
    # 先买入
    oid = broker.place_order("600519", "BUY", 100.0, 500)
    broker.fill_order(int(oid), 100.0)
    broker.end_of_day("2026-09-15")

    sig = {"code": "600519", "signal": "AVOID", "confidence": "中",
           "buy_zone": None, "name": "测试"}
    results = execute_signals([sig], broker)
    assert results[0]["action"] == "filled"
    assert results[0]["volume"] == 500


def test_execute_confidence_sizing(tmp_db, monkeypatch):
    """confidence 影响仓位大小"""
    monkeypatch.setattr("src.paper.engine._get_current_price", lambda code: 100.0)
    cfg = {"max_position_pct": 40, "slippage_pct": 0.1,
           "commission_rate": 0.00025, "commission_min": 5.0,
           "stamp_tax_rate": 0.005, "transfer_fee_rate": 0.00001,
           "max_total_position_pct": 80, "force_drawdown_pct": -99}

    # 高 confidence
    broker1 = PaperBroker()
    broker1._cfg = cfg
    sig = {"code": "600519", "signal": "BUY", "confidence": "高",
           "buy_zone": None, "name": "测试"}
    execute_signals([sig], broker1)
    pos1 = broker1._positions.get("600519")

    # 低 confidence（用不同股票避免共享 DB 冲突）
    broker2 = PaperBroker()
    broker2._cfg = cfg
    sig2 = {"code": "000001", "signal": "BUY", "confidence": "低",
            "buy_zone": None, "name": "测试"}
    execute_signals([sig2], broker2)
    pos2 = broker2._positions.get("000001")

    assert pos1 is not None and pos2 is not None
    assert pos1["volume"] > pos2["volume"]


def test_execute_empty_signals(tmp_db):
    assert execute_signals([], PaperBroker()) == []


# ---- run_paper_trading ----

def test_run_paper_trading_no_config(tmp_db, monkeypatch):
    """配置为空时跳过"""
    monkeypatch.setattr("src.paper.engine.load_config", lambda: {})
    result = run_paper_trading(config={})
    assert result["signals"] == []
    assert result["executions"] == []


from src.paper.broker import PaperBroker
