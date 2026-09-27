"""纸盘模拟页 + 回测接口单测。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


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


def _seed_screening(db_mod, run_id="r1", run_date="2026-06-01",
                    rows=(("600519", "名600519", 90.0),)):
    with db_mod.db_conn() as conn:
        for code, name, score in rows:
            conn.execute(
                "INSERT INTO screening_result "
                "(run_id, run_date, code, name, score)"
                " VALUES (?,?,?,?,?)",
                (run_id, run_date, code, name, score))


class TestPaperPage:
    def test_page(self, client):
        resp = client.get("/paper")
        assert resp.status_code == 200
        assert "模拟交易" in resp.text
        assert "live 模拟账户" in resp.text

    def test_universe(self, client):
        from src.models import database as db_mod
        _seed_screening(db_mod, rows=[("600519", "名600519", 90.0),
                                      ("000858", "名000858", 80.0)])
        # run_log completed 行（universe 取最新完成轮）
        db_mod.RunLogDAO().start_run("r1")
        db_mod.RunLogDAO().complete_run("r1", 5527, 2, 0)
        resp = client.get("/api/paper/universe")
        assert resp.status_code == 200
        assert resp.json()["codes"] == ["600519", "000858"]

    def test_universe_empty(self, client):
        resp = client.get("/api/paper/universe")
        assert resp.json()["codes"] == []


class TestBacktestAPI:
    def test_validation(self, client):
        base = {"strategy": "ma", "codes": "600519",
                "start": "2026-06-01", "end": "2026-06-30",
                "initial_cash": 100000.0}
        for bad in ({"strategy": "xxx"}, {"start": "2026-06-30", "end": "2026-06-01"},
                    {"initial_cash": -1}, {"top_n": 0}, {"top_n": 51},
                    {"short_window": 20, "long_window": 5},
                    {"buy_volume": 0}, {"codes": "abc"}):
            payload = dict(base)
            payload.update(bad)
            assert client.post("/api/paper/backtest",
                               json=payload).status_code == 400, bad

    def test_run_small(self, client):
        import time
        from src.models import database as db_mod
        _seed_kline(db_mod, "600519", [10.0] * 25)
        _seed_screening(db_mod)
        resp = client.post("/api/paper/backtest", json={
            "strategy": "ma", "codes": "600519",
            "start": "2026-06-01", "end": "2026-06-25",
            "initial_cash": 100000.0})
        assert resp.status_code == 200
        bid = resp.json()["backtest_id"]
        deadline = time.time() + 30
        out = {"status": "pending"}
        while time.time() < deadline:
            out = client.get(f"/api/paper/backtest/{bid}").json()
            if out.get("backtest_id"):
                break
            time.sleep(0.5)
        assert out.get("backtest_id") == bid
        assert len(out["nav"]) == 25
        assert out["summary"]["trades"] == 0

    def test_unknown_id(self, client):
        assert client.get(
            "/api/paper/backtest/bt9999").json()["status"] == "pending"
