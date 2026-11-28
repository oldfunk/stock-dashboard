"""T104：母项目接口探针（形状/取值/新鲜度）。"""
import sqlite3

import pytest

from paper_trading.integration.probes import (
    BENCHMARK_CODES,
    probe_all,
    probe_benchmark,
    probe_sector,
    probe_strategy,
)

PAYLOAD = ('{"signal": "BUY", "confidence": "中", "buy_zone": "10-12元", '
           '"target_price": "15-16元", "stop_loss": "跌破9元"}')


def _mother(tmp_path, *, payload=PAYLOAD, signals=None, codes=BENCHMARK_CODES,
            screening_cols=("code", "run_date", "ai_trade_strategy",
                            "ai_failed", "score", "status"),
            benchmark_rows=None):
    mdir = tmp_path / "mother"
    db = mdir / "data" / "db" / "stock_dashboard.db"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.execute(f"CREATE TABLE screening_result ({', '.join(screening_cols)})")
    rows = signals or [("603998", payload)]
    for code, pl in rows:
        vals = {"code": code, "run_date": "2026-11-23",
                "ai_trade_strategy": pl, "ai_failed": 0, "score": 67.4,
                "status": "active"}
        cols = [x for x in screening_cols]
        c.execute(f"INSERT INTO screening_result ({','.join(cols)})"
                  f" VALUES ({','.join('?' * len(cols))})",
                  [vals[x] for x in cols])
    c.execute("CREATE TABLE stock_snapshot (code TEXT, sector TEXT,"
              " snapshot_date TEXT)")
    c.execute("INSERT INTO stock_snapshot VALUES ('603998','生物制药','2026-11-23')")
    c.execute("INSERT INTO stock_snapshot VALUES ('000001',NULL,'2026-11-23')")
    c.execute("CREATE TABLE benchmark_daily (code TEXT, trade_date TEXT,"
              " close REAL)")
    for code in (codes if benchmark_rows is None else benchmark_rows):
        c.execute("INSERT INTO benchmark_daily VALUES (?,?,?)",
                  (code, "2026-11-23", 4000.0))
    c.commit()
    c.close()
    return mdir


def test_probe_all_good(tmp_path):
    out = probe_all(_mother(tmp_path), today="2026-11-24")
    assert out["ok"] is True and out["available"] is True
    assert out["parts"]["strategy"]["asof"] == "2026-11-23"
    assert out["parts"]["strategy"]["shape"]["signals"] == {"BUY": 1}
    assert out["parts"]["sector"]["shape"]["coverage"] == 0.5
    assert out["parts"]["benchmark"]["ok"] is True
    assert out["stale"] is False


def test_probe_bad_json_is_shape_error(tmp_path):
    out = probe_strategy(_mother(tmp_path, payload="{not-json"), today="2026-11-24")
    assert out["ok"] is False
    assert any(e["code"] == "bad_json" for e in out["errors"])


def test_probe_unknown_signal_is_shape_error(tmp_path):
    out = probe_strategy(
        _mother(tmp_path, payload='{"signal":"MAYBE","buy_zone":"1-2"}'),
        today="2026-11-24")
    assert out["ok"] is False
    assert any(e["code"] == "unknown_signal" for e in out["errors"])


def test_probe_missing_column_is_shape_error(tmp_path):
    out = probe_strategy(
        _mother(tmp_path, screening_cols=("code", "ai_trade_strategy",
                                          "ai_failed", "score", "status")),
        today="2026-11-24")
    assert out["ok"] is False
    assert any(e["code"] == "missing_columns" for e in out["errors"])


def test_probe_benchmark_requires_all_contract_codes(tmp_path):
    out = probe_benchmark(
        _mother(tmp_path, codes=("sh000300", "000001")), today="2026-11-24")
    assert out["ok"] is False
    missing = {e["code"] for e in out["errors"]}
    assert "missing_code" in missing


def test_probe_sector_flags_low_coverage(tmp_path):
    out = probe_sector(
        _mother(tmp_path, codes=BENCHMARK_CODES), today="2026-11-24")
    # 1/2 有行业 ⇒ 50%，不低于阈值；把两行都改成 NULL 再验一次
    assert out["ok"] is True
    db = tmp_path / "mother" / "data" / "db" / "stock_dashboard.db"
    c = sqlite3.connect(db)
    c.execute("UPDATE stock_snapshot SET sector=NULL")
    c.commit()
    c.close()
    out2 = probe_sector(tmp_path / "mother", today="2026-11-24")
    assert out2["ok"] is False
    assert any(e["code"] == "low_coverage" for e in out2["warnings"])


def test_probe_sector_counts_all_codes_not_only_latest_date(tmp_path):
    """`stock_snapshot` 是每 code 一行的最新快照，覆盖率必须按全表算。"""
    mdir = _mother(tmp_path, codes=BENCHMARK_CODES)
    db = mdir / "data" / "db" / "stock_dashboard.db"
    c = sqlite3.connect(db)
    c.execute("INSERT INTO stock_snapshot VALUES ('600036','金融行业','2026-11-01')")
    c.commit()
    c.close()
    out = probe_sector(mdir, today="2026-11-24")
    assert out["shape"]["rows"] == 3
    assert out["shape"]["fresh_rows"] == 2
    assert out["shape"]["coverage"] == pytest.approx(2 / 3, abs=1e-4)


def test_probe_mother_unavailable(tmp_path):
    out = probe_all(tmp_path / "nope", today="2026-11-24")
    assert out["ok"] is False and out["available"] is False
    for part in out["parts"].values():
        assert any(e["code"] == "mother_db_unavailable" for e in part["errors"])


def test_probe_stale_after_window(tmp_path):
    out = probe_all(_mother(tmp_path), today="2026-12-01")
    assert out["stale"] is True
    assert out["parts"]["strategy"]["stale"] is True
