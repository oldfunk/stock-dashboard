"""P2/T98：当日分时权益（成交时间线 + 分钟线重建）。"""
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paper_trading.dashboard as d  # noqa: E402
from paper_trading.broker.paper_broker import PaperBroker  # noqa: E402
from paper_trading.data.db_manager import DataDBManager  # noqa: E402
from paper_trading.models import TradingConfig  # noqa: E402


def _minute_row(ts, close):
    return {"ts": ts, "open": close, "high": close, "low": close,
            "close": close, "volume": 1000, "amount": close * 1000}


def test_intraday_reconstructs_from_fills_and_minutes(tmp_path):
    acct = PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=10000.0))
    acct.apply_external_fill(symbol="600519", direction=1, volume=100,
                             price=10.0, commission=5.0,
                             filled_at=datetime(2026, 11, 20, 10, 0))
    data = DataDBManager(tmp_path / "data.db")
    data.upsert_minute_bars("600519", [
        _minute_row("2026-11-20T09:35", 10.0),
        _minute_row("2026-11-20T10:00", 12.0),
    ], period="5")

    out = d._intraday_payload(str(acct.db_path), data.db_path, "2026-11-20")
    assert out["available"] is True and out["reconstruction_ok"] is True
    assert out["fills"] == 1
    assert [p["ts"][11:16] for p in out["series"]] == ["09:30", "09:35", "10:00"]
    assert out["start"]["cash"] == 10000.0
    assert out["start"]["positions"] == {}
    # 10:00 买入后：现金 8995 + 100×12 = 10195
    assert out["series"][-1]["total"] == 10195.0
    assert out["series"][-1]["cash"] == 8995.0


def test_intraday_without_minutes_is_unavailable(tmp_path):
    acct = PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=10000.0))
    data = DataDBManager(tmp_path / "data.db")
    out = d._intraday_payload(str(acct.db_path), data.db_path, "2026-11-21")
    assert out["available"] is False and out["series"] == []


def test_intraday_weekend_falls_back_to_last_trading_day(tmp_path, monkeypatch):
    """周末默认请求今天时，显示最近有分钟线的交易日（并标明 fallback）。"""
    class _FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 21)

    monkeypatch.setattr(d, "date", _FakeDate)
    acct = PaperBroker(db_path=str(tmp_path / "acct.db"),
                       config=TradingConfig(initial_cash=10000.0))
    data = DataDBManager(tmp_path / "data.db")
    data.upsert_minute_bars("600519", [
        _minute_row("2026-11-20T09:35", 10.0),
        _minute_row("2026-11-20T10:00", 12.0),
    ], period="5")
    out = d._intraday_payload(str(acct.db_path), data.db_path)   # day=None
    assert out["available"] is True and out["date"] == "2026-11-20"
    assert out.get("fallback") is True and out.get("requested_date") == "2026-11-21"
