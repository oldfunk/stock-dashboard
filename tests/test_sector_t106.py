"""T106：行业分布（持仓市值 + 母库 sector，NULL 归“未知”）。"""
import sqlite3

import pytest

import paper_trading.dashboard as d
from paper_trading.integration import mother as m


class _Broker:
    @staticmethod
    def get_all_positions():
        return []


class _Bridge:
    data_db = None
    broker = _Broker()

    def get_status(self):
        return {"positions": [
            {"symbol": "601899", "total_volume": 400, "market_value": 9688.0},
            {"symbol": "000792", "total_volume": 100, "market_value": 2153.0},
            {"symbol": "920999", "total_volume": 100, "market_value": 1000.0},
        ]}


def _mother(tmp_path, rows=(("601899", "有色金属", "2026-11-23"),
                            ("000792", "农药化肥", "2026-11-23"))):
    root = tmp_path / "mother"
    db = root / "data" / "db" / "stock_dashboard.db"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE stock_snapshot (code TEXT, sector TEXT,"
              " snapshot_date TEXT)")
    for row in rows:
        c.execute("INSERT INTO stock_snapshot VALUES (?,?,?)", row)
    c.commit()
    c.close()
    return root


def test_sector_aggregate_unknown_last():
    out = d._aggregate_sectors(
        [{"symbol": "601899", "market_value": 9688.0},
         {"symbol": "000792", "market_value": 2153.0},
         {"symbol": "920999", "market_value": 1000.0}],
        {"601899": "有色金属", "000792": "农药化肥"})
    assert out["total_mv"] == pytest.approx(12841.0)
    assert out["coverage"] == {"with_sector": 2, "total": 3}
    assert [x["sector"] for x in out["sectors"]] == ["有色金属", "农药化肥", "未知"]
    assert out["sectors"][-1]["market_value"] == pytest.approx(1000.0)
    assert sum(x["market_value"] for x in out["sectors"]) == pytest.approx(12841.0)
    assert sum(x["weight"] for x in out["sectors"]) == pytest.approx(1.0, abs=1e-5)


def test_sector_payload_uses_mother_and_unknown_bucket(tmp_path):
    out = d._sector_payload(_Bridge(), mother=_mother(tmp_path))
    assert out["available"] is True
    assert out["asof"] == "2026-11-23"
    assert out["coverage"] == {"with_sector": 2, "total": 3}
    assert out["sectors"][-1]["sector"] == "未知"
    assert sum(x["market_value"] for x in out["sectors"]) == pytest.approx(
        out["total_mv"])


def test_sector_payload_unavailable_when_mother_missing(tmp_path):
    out = d._sector_payload(_Bridge(), mother=tmp_path / "nope")
    assert out["available"] is False
    assert out["sectors"] == [] and out["total_mv"] == 0.0


def test_mother_sectors_null_stays_null(tmp_path):
    root = _mother(tmp_path, rows=(("601899", "有色金属", "2026-11-23"),
                                   ("000792", None, "2026-11-23")))
    blk = m.sectors(["601899", "000792", "920999"], mother=root)
    assert blk["available"] is True and blk["asof"] == "2026-11-23"
    assert blk["data"]["601899"] == "有色金属"
    assert blk["data"]["000792"] is None      # NULL 不回退
    assert blk["data"]["920999"] is None      # 无行也是 None
    assert "未知" in blk["note"] or "无行业" in blk["note"]
