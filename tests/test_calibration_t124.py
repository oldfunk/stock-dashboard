"""T124：P5.5 AI 成绩校准（去重/历史 verdict 回填/样本阈值）。"""
import json
import sqlite3

import pytest

from paper_trading.integration import mother as m
from paper_trading.strategy import calibration as cal


def _db(tmp_path, rows, *, bench_dup=True):
    sd = tmp_path / "sd"
    (sd / "data" / "db").mkdir(parents=True)
    p = sd / "data" / "db" / "stock_dashboard.db"
    c = sqlite3.connect(p)
    c.execute("""CREATE TABLE analysis_outcome (
        analysis_id INTEGER, benchmark_code TEXT, stock_code TEXT,
        stock_name TEXT, analysis_date TEXT, signal TEXT, confidence TEXT,
        verdict TEXT, entry_price REAL, ret_20 REAL, ret_60 REAL, ret_120 REAL,
        excess_20 REAL, excess_60 REAL, excess_120 REAL)""")
    c.execute("""CREATE TABLE stock_analysis_history (
        id INTEGER PRIMARY KEY, stock_code TEXT, run_id TEXT,
        analysis_date TEXT, score REAL, ai_analysis TEXT,
        ai_trade_strategy TEXT, created_at TEXT, model TEXT)""")
    for r in rows:
        for bc in (("sh000300", "sh000922") if bench_dup else ("sh000300",)):
            c.execute("INSERT INTO analysis_outcome VALUES "
                      "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (r["id"], bc, r["code"], "名", r["date"], r["signal"],
                       r.get("conf", "中"), r.get("verdict", ""), 10.0,
                       r.get("ret20"), None, None, r.get("ex20"), None, None))
        if r.get("ai"):
            c.execute("INSERT INTO stock_analysis_history VALUES "
                      "(?,?,?,?,?,?,?,?,?)",
                      (r["id"], r["code"], "run", r["date"], 70,
                       json.dumps(r["ai"], ensure_ascii=False), None,
                       "2026-01-01", "test"))
    c.commit()
    c.close()
    m.reset_cache()
    return sd


def _rows(n=40):
    out = []
    for i in range(n):
        if i < 14:
            sig, verdict, ex = "BUY", "通过：有安全边际", 0.06
        elif i < 28:
            sig, verdict, ex = "AVOID", "不通过：高估", -0.02
        else:
            sig, verdict, ex = "HOLD", "灰色地带：观察", 0.01
        out.append({"id": 100 + i, "code": f"60{i:04d}", "date": "2026-10-01",
                    "signal": sig, "verdict": verdict, "ret20": ex + 0.03,
                    "ex20": ex})
    return out


def test_outcome_samples_dedup_and_recover(tmp_path):
    good = _rows()
    old = {"id": 900, "code": "000792", "date": "2026-07-14", "signal": "",
           "verdict": "", "ret20": -0.03, "ex20": -0.05,
           "ai": {"verdict": "不通过：高估",
                  "trade_strategy": {"signal": "AVOID"}}}
    sd = _db(tmp_path, good + [old])
    try:
        blk = m.outcome_samples(mother=sd)
        assert blk["available"] is True
        data = blk["data"]
        # 基准过滤后每个 analysis 只剩一条；去重计数为 0 属预期
        assert data["raw_count"] == 41 and data["unique_count"] == 41
        rec = [r for r in data["rows"] if r["analysis_id"] == 900][0]
        assert rec["verdict"] == "不通过：高估"
        assert rec["signal"] == "AVOID"
        assert data["recovered"] == 1
    finally:
        m.reset_cache()


def test_outcome_samples_dedup_without_benchmark_column(tmp_path):
    sd = tmp_path / "old"
    (sd / "data" / "db").mkdir(parents=True)
    p = sd / "data" / "db" / "stock_dashboard.db"
    c = sqlite3.connect(p)
    c.execute("""CREATE TABLE analysis_outcome (
        analysis_id INTEGER, stock_code TEXT, stock_name TEXT,
        analysis_date TEXT, signal TEXT, confidence TEXT, verdict TEXT,
        entry_price REAL, ret_20 REAL, ret_60 REAL, ret_120 REAL,
        excess_20 REAL, excess_60 REAL, excess_120 REAL)""")
    for _ in range(2):
        c.execute("INSERT INTO analysis_outcome VALUES "
                  "(1,'600519','贵州茅台','2026-10-01','BUY','中','通过',10,0.05,"
                  "NULL,NULL,0.02,NULL,NULL)")
    c.commit()
    c.close()
    m.reset_cache()
    try:
        data = m.outcome_samples(mother=sd)["data"]
        assert data["raw_count"] == 2 and data["unique_count"] == 1
        assert data["rows"][0]["analysis_id"] == 1
    finally:
        m.reset_cache()


def test_calibration_sufficient_and_spread(tmp_path):
    sd = _db(tmp_path, _rows())
    try:
        out = cal.build_calibration(mother=sd)
        assert out["available"] is True and out["sufficient"] is True
        assert out["n"] == 40
        assert out["by_signal"]["BUY"]["n"] == 14
        assert out["by_signal"]["AVOID"]["n"] == 14
        assert out["by_signal"]["BUY"]["hit_rate"] == 1.0
        assert out["by_signal"]["AVOID"]["hit_rate"] == 1.0
        assert out["by_signal"]["BUY"]["avg_excess_20"] == 0.06
        assert out["spread_buy_minus_avoid"] == pytest.approx(0.08)
        assert out["duplicates_dropped"] == 0
    finally:
        m.reset_cache()


def test_calibration_includes_signal_only_old_samples(tmp_path):
    old = {"id": 900, "code": "000792", "date": "2026-07-14", "signal": "AVOID",
           "verdict": "", "ret20": -0.03, "ex20": -0.05}
    sd = _db(tmp_path, _rows(30) + [old])
    try:
        out = cal.build_calibration(mother=sd)
        assert out["available"] is True
        assert out["n"] == 31
        # 旧样本没有 verdict，但仍按 signal 进入 AVOID 统计
        assert out["by_signal"]["AVOID"]["n"] == 15
    finally:
        m.reset_cache()


def test_calibration_insufficient_sample(tmp_path):
    sd = _db(tmp_path, _rows(10))
    try:
        out = cal.build_calibration(mother=sd)
        assert out["available"] is True
        assert out["sufficient"] is False
        assert "样本不足" in out["warning"]
    finally:
        m.reset_cache()


def test_calibration_unavailable_without_mother(tmp_path):
    out = cal.build_calibration(mother=tmp_path / "nope")
    assert out["available"] is False
    assert out["reason"]


def test_scorecard_payload_minifies_and_keeps_warning(monkeypatch):
    from paper_trading.cli import _scorecard_payload

    fake = {"available": True, "sufficient": True, "n": 390,
            "window": {"start": "2026-07-08", "end": "2026-09-29"},
            "by_signal": {"BUY": {"n": 23, "avg_excess_20": 0.1, "hit_rate": 0.9},
                          "HOLD": {"n": 152, "avg_excess_20": 0.05},
                          "AVOID": {"n": 215, "avg_excess_20": 0.01,
                                    "hit_rate": 0.5}},
            "spread_buy_minus_avoid": 0.09,
            "warnings": ["BUY 样本仅 23 条"], "asof": "2026-11-26"}
    monkeypatch.setattr(cal, "build_calibration", lambda **k: fake)
    out = _scorecard_payload()
    assert out["available"] is True and out["buy"]["n"] == 23
    assert out["warnings"] == ["BUY 样本仅 23 条"]
    assert "历史统计" in out["note"]

