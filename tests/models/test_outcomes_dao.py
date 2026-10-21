"""前向收益/分红 DAO 与刷新服务单测（2026-10-21）。

hermetic：tmp 库，K 线用假数据，不联网。
覆盖：DAO 幂等、冻结（已算出的收益不重算）、未到期 None、增量补 60 日。
"""

import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "o.db"))
    db_mod.init_database()
    return db_mod


class TestBenchmarkDAO:
    def test_upsert_and_order(self, db):
        dao = db.BenchmarkDailyDAO()
        assert dao.get_series("sh000300") == []
        n = dao.upsert_many("sh000300", [
            {"trade_date": "2026-10-09", "close": 105.0},
            {"trade_date": "2026-09-30", "close": 100.0},
            {"trade_date": "2026-10-10", "close": None},  # 跳过
        ])
        assert n == 2
        rows = dao.get_series("sh000300")
        assert [r["trade_date"] for r in rows] == ["2026-09-30", "2026-10-09"]
        assert dao.latest_date("sh000300") == "2026-10-09"
        # 幂等
        assert dao.upsert_many("sh000300", [
            {"trade_date": "2026-10-09", "close": 106.0}]) == 1
        assert dao.get_series("sh000300")[-1]["close"] == 106.0


class TestDividendDAO:
    def test_upsert_latest_by_codes(self, db):
        dao = db.DividendDAO()
        assert dao.count() == 0
        dao.upsert_many([
            {"stock_code": "000001", "report_date": "2024-12-31",
             "cash_per_share": 0.1, "eps": 1.0, "reported_yield": 1.0},
            {"stock_code": "000001", "report_date": "2025-12-31",
             "cash_per_share": 0.2, "eps": 1.0, "reported_yield": 2.0},
            {"stock_code": "000002", "report_date": "2025-12-31",
             "cash_per_share": 0.3, "eps": 1.0, "reported_yield": 3.0},
        ])
        assert dao.count() == 3
        assert dao.count("2025-12-31") == 2
        latest = dao.latest_by_codes(["000001", "000002", "000003"],
                                     "2025-12-31")
        assert latest["000001"]["cash_per_share"] == 0.2
        assert latest["000002"]["cash_per_share"] == 0.3
        assert "000003" not in latest
        assert dao.get_latest("000001")["report_date"] == "2025-12-31"


class TestOutcomeDAO:
    def _row(self, aid=1, bench="sh000300", **kw):
        base = {
            "analysis_id": aid, "benchmark_code": bench,
            "stock_code": "600519", "stock_name": "贵州茅台",
            "analysis_date": "2026-09-01", "signal": "BUY",
            "confidence": "高", "verdict": "通过",
            "entry_date": "2026-09-02", "entry_price": 10.0,
            "ret_20": 5.0, "ret_60": None, "ret_120": None,
            "bench_20": 1.0, "bench_60": None, "bench_120": None,
            "excess_20": 4.0, "excess_60": None, "excess_120": None,
        }
        base.update(kw)
        return base

    def test_upsert_get_recent(self, db):
        dao = db.AnalysisOutcomeDAO()
        assert dao.upsert_many([
            self._row(1), self._row(2, analysis_date="2026-09-02"),
            self._row(3, bench="sh000922"),
        ]) == 3
        assert len(dao.get_all("sh000300")) == 2
        assert len(dao.get_all()) == 3
        recent = dao.list_recent(limit=10, benchmark_code="sh000300")
        assert [r["analysis_id"] for r in recent] == [2, 1]
        assert dao.get(1, "sh000300")["excess_20"] == 4.0
        assert dao.get(1, "sh000922") is None

    def test_upsert_is_idempotent(self, db):
        dao = db.AnalysisOutcomeDAO()
        row = self._row(1)
        dao.upsert_many([row])
        row["ret_20"] = 8.0
        row["excess_20"] = 7.0
        dao.upsert_many([row])
        assert len(dao.get_all("sh000300")) == 1
        assert dao.get(1, "sh000300")["ret_20"] == 8.0


def _stock_bars(n, base=10.0, step=0.1):
    start = datetime(2026, 9, 2)
    return [{"trade_date": (start + timedelta(days=i)).strftime("%Y-%m-%d"),
             "close": base + i * step} for i in range(n)]


def _bench_bars(n):
    start = datetime(2026, 9, 2)
    return [{"trade_date": (start + timedelta(days=i)).strftime("%Y-%m-%d"),
             "close": 100.0 + i} for i in range(n)]


class TestRefreshOutcomes:
    def _insert_analysis(self, db):
        from src.utils import now_cn
        with db.db_conn() as conn:
            conn.execute("""
                INSERT INTO stock_analysis_history
                (stock_code, run_id, analysis_date, score, ai_analysis,
                 ai_trade_strategy, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("600519", "r1", "2026-09-01", 90.0,
                  json.dumps({"verdict": "通过"}, ensure_ascii=False),
                  json.dumps({"signal": "BUY", "confidence": "高"},
                             ensure_ascii=False),
                  "test", now_cn().isoformat()))
        return db.StockAnalysisHistoryDAO().list_all()[0]

    def test_incomplete_then_incremental_with_freeze(self, db, monkeypatch):
        import src.outcome_service as svc
        import src.collector.akshare_fetcher as fetcher

        analysis = self._insert_analysis(db)
        db.BenchmarkDailyDAO().upsert_many("sh000300", _bench_bars(80))
        cfg = {"outcomes": {
            "benchmarks": [{"code": "sh000300", "name": "沪深300"}],
            "horizons": [20, 60],
        }}
        monkeypatch.setattr(svc.time, "sleep", lambda *_: None)

        # 第一次：只有 30 根 K 线，20 日到期，60 日还没到
        monkeypatch.setattr(fetcher, "fetch_kline_data",
                            lambda *a, **k: _stock_bars(30))
        stats1 = svc.refresh_outcomes(cfg)
        assert stats1["rows"] == 1
        row1 = db.AnalysisOutcomeDAO().get(analysis["id"], "sh000300")
        assert row1["ret_20"] is not None
        assert row1["ret_60"] is None
        frozen_20 = row1["ret_20"]

        # 第二次：80 根 K 线，且人为改坏 20 日退出价；已冻结的 20 日不得变
        bars = _stock_bars(80)
        bars[20]["close"] = 99.0
        monkeypatch.setattr(fetcher, "fetch_kline_data",
                            lambda *a, **k: bars)
        stats2 = svc.refresh_outcomes(cfg)
        assert stats2["rows"] == 1
        row2 = db.AnalysisOutcomeDAO().get(analysis["id"], "sh000300")
        assert row2["ret_20"] == frozen_20
        assert row2["ret_60"] is not None
        assert row2["bench_60"] is not None
        assert row2["excess_60"] is not None

    def test_all_horizons_complete_skips_fetch(self, db, monkeypatch):
        import src.outcome_service as svc
        import src.collector.akshare_fetcher as fetcher

        self._insert_analysis(db)
        db.BenchmarkDailyDAO().upsert_many("sh000300", _bench_bars(80))
        cfg = {"outcomes": {
            "benchmarks": [{"code": "sh000300", "name": "沪深300"}],
            "horizons": [20, 60],
        }}
        monkeypatch.setattr(svc.time, "sleep", lambda *_: None)
        calls = []

        def fake_fetch(*a, **k):
            calls.append(1)
            return _stock_bars(80)

        monkeypatch.setattr(fetcher, "fetch_kline_data", fake_fetch)
        svc.refresh_outcomes(cfg)
        assert len(calls) == 1
        # 第二次全完整，不应再拉 K 线
        svc.refresh_outcomes(cfg)
        assert len(calls) == 1


class TestIsComplete:
    def test_requires_all_horizons(self):
        from src.outcome_service import _is_complete
        full = {"ret_20": 1, "bench_20": 1, "excess_20": 0,
                "ret_60": 1, "bench_60": 1, "excess_60": 0}
        assert _is_complete(full, (20, 60)) is True
        partial = dict(full, excess_60=None)
        assert _is_complete(partial, (20, 60)) is False
        assert _is_complete(None, (20,)) is False
