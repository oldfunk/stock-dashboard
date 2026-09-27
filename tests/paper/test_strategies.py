"""纸盘策略 + 回测引擎单测（合成数据，不读 ambient）。"""
import pytest

from src.paper.strategies import MACrossStrategy, plan_value_rotation
from src.paper.types import Bar, SignalType


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    return db_mod


def _bars(closes, code="M", start="2026-06-01"):
    from datetime import datetime, timedelta
    out = []
    d0 = datetime.fromisoformat(start)
    for i, c in enumerate(closes):
        d = d0 + timedelta(days=i)
        out.append(Bar(symbol=code, timestamp=d, open=c, high=c,
                       low=c, close=c, volume=1000))
    return out


def _seed_kline(db_mod, code, closes, start="2026-06-01"):
    from datetime import datetime, timedelta
    d0 = datetime.fromisoformat(start)
    with db_mod.db_conn() as conn:
        for i, c in enumerate(closes):
            d = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO kline_daily (code, trade_date, open, high, low,"
                " close, volume, amount, turnover)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (code, d, c, c, c, c, 1000.0, c * 1000.0, 1.0))


class TestMA:
    def test_golden_cross(self):
        ma = MACrossStrategy()
        bars = _bars([10.0] * 25 + [10.5] * 5)
        sigs = ma.generate_signals({"M": bars})
        assert set(sigs) == set()
        sigs = ma.generate_signals({"M": bars[:26]})
        assert sigs["M"].direction == SignalType.BUY

    def test_death_cross(self):
        ma = MACrossStrategy()
        bars = _bars([10.5] * 25 + [10.0] * 5)
        sigs = ma.generate_signals({"M": bars[:26]})
        assert sigs["M"].direction == SignalType.SELL

    def test_insufficient_and_flat(self):
        ma = MACrossStrategy()
        assert ma.generate_signals({"M": _bars([10.0] * 10)}) == {}
        assert ma.generate_signals({"M": _bars([10.0] * 30)}) == {}


class TestValuePlanner:
    def test_buy_and_sell(self):
        sigs = plan_value_rotation(
            {"A": 1000}, [("B", 90), ("A", 80)],
            {"A": 10.0, "B": 20.0}, 100000.0, 110000.0,
            top_n=1, dropout_n=1)
        by_code = {s.symbol: s for s in sigs}
        assert by_code["A"].direction == SignalType.SELL
        assert by_code["A"].volume == 1000
        assert by_code["B"].direction == SignalType.BUY
        assert by_code["B"].volume == 5200
        assert by_code["B"].volume % 100 == 0

    def test_empty(self):
        assert plan_value_rotation({}, [], {}, 0, 0) == []
        assert plan_value_rotation({"A": 1000}, [], {"A": 10.0},
                                    100000.0, 110000.0) == []


class TestBacktest:
    def test_ma_run(self, db):
        from src.paper.backtest import run_backtest
        closes = [10.0] * 25 + [10.5] * 5
        _seed_kline(db, "M", closes)
        out = run_backtest("ma", ["M"], "2026-06-01", "2026-06-30",
                           100000.0, {})
        assert out["summary"]["trades"] == 1
        assert len(out["nav"]) == 30
        assert out["trades"][0]["direction"] == "买入"
        assert out["summary"]["fee_total"] > 0

    def test_value_rotation(self, db):
        from src.paper.backtest import run_backtest
        for c in ("A", "B", "C", "D", "E"):
            _seed_kline(db, c, [10.0] * 40, start="2026-06-01")
        with db.db_conn() as conn:
            for run_id, run_date, rows in (
                    ("rj", "2026-06-01",
                     [("A", 90), ("B", 85), ("C", 80), ("D", 75), ("E", 70)]),
                    ("jy", "2026-07-01",
                     [("B", 90), ("C", 85), ("D", 80), ("E", 75), ("A", 70)])):
                for code, score in rows:
                    conn.execute(
                        "INSERT INTO screening_result "
                        "(run_id, run_date, code, name, score)"
                        " VALUES (?,?,?,?,?)",
                        (run_id, run_date, code, "名" + code, score))
        out = run_backtest("value", ["A", "B", "C", "D", "E"],
                           "2026-06-01", "2026-07-20", 100000.0,
                           {"top_n": 4, "dropout_n": 4})
        assert out["summary"]["trades"] == 6
        assert len(out["nav"]) == 40

    def test_validation(self, db):
        from src.paper.backtest import run_backtest
        with pytest.raises(ValueError):
            run_backtest("xxx", ["M"], "2026-06-01", "2026-06-30")
        with pytest.raises(ValueError):
            run_backtest("ma", [], "2026-06-01", "2026-06-30")
        with pytest.raises(ValueError):
            run_backtest("ma", ["M"], "2026-06-30", "2026-06-01")
        with pytest.raises(ValueError):
            run_backtest("ma", ["ZZZ"], "2026-06-01", "2026-06-30")
