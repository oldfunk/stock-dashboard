"""市场状态向量定义回归测试（R1-1）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "ms.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def test_schema_covers_five_blocks():
    from src.research.market_state import StateSchema
    schema = StateSchema()
    for block in ("valuation", "fundamentals", "breadth", "liquidity", "quality"):
        assert schema.list(block), f"分块无字段: {block}"
    assert schema.get("pe_median").block == "valuation"
    assert schema.get("pe_median").source == "stock_snapshot.pe"


def test_schema_rejects_bad_registration():
    from src.research.market_state import StateError, StateField, StateSchema
    schema = StateSchema()
    with pytest.raises(StateError):
        schema.register(StateField("x", "bogus", "f", "s"))
    with pytest.raises(StateError):
        schema.register(StateField("y", "valuation", "f", "s",
                                   missing_policy="zero_fill"))
    with pytest.raises(StateError):
        schema.register(StateField("pe_median", "valuation", "f", "s"))


def test_compute_vector_missing_is_null_not_zero(db):
    from src.research.market_state import compute_vector
    vector = compute_vector("2026-10-26")
    assert vector.metrics == {}
    assert "pe_median" in vector.missing
    assert vector.status == "pass"
    assert vector.coverage == 0.0


def test_compute_vector_from_snapshot(db):
    from src.research.market_state import compute_vector

    def snap(code, name, pe, pb, roe):
        return {"code": code, "name": name, "market": "A", "sector": "s",
                "pe": pe, "pb": pb, "ps": 1.0, "market_cap": 1.0,
                "circulating_cap": 1.0, "roe": roe, "revenue": 1.0,
                "revenue_growth": 5.0, "profit": 1.0, "profit_growth": 5.0,
                "debt_ratio": 30.0, "current_price": 1.0, "high_52w": 1.0,
                "low_52w": 1.0, "is_st": False, "list_date": "2010-01-01",
                "snapshot_date": "2026-10-26"}

    db_mod.StockSnapshotDAO().save_batch([
        snap("600519", "A", 20.0, 3.0, 10.0),
        snap("000001", "B", 10.0, 1.0, 20.0),
    ])
    vector = compute_vector("2026-10-26")
    assert vector.metrics["pe_median"] == 15.0
    assert vector.metrics["pb_median"] == 2.0
    assert vector.metrics["roe_median"] == 15.0
    assert "total_turnover_yi" in vector.missing


def test_fail_closed_field_makes_vector_invalid(db):
    from src.research.market_state import StateField, StateSchema, compute_vector
    schema = StateSchema([
        StateField("pe_median", "valuation", "f", "s", missing_policy="fail_closed")])
    vector = compute_vector("2026-10-26", schema=schema)
    assert vector.status == "invalid"
    assert vector.fail_closed_missing == ["pe_median"]


def test_compute_vector_uses_latest_snapshot_date(db):
    from src.research.market_state import compute_vector

    def snap(code, pe, day):
        return {"code": code, "name": "X", "market": "A", "sector": "s", "pe": pe,
                "pb": 1.0, "ps": 1.0, "market_cap": 1.0, "circulating_cap": 1.0,
                "roe": 10.0, "revenue": 1.0, "revenue_growth": 5.0,
                "profit": 1.0, "profit_growth": 5.0, "debt_ratio": 30.0,
                "current_price": 1.0, "high_52w": 1.0, "low_52w": 1.0,
                "is_st": False, "list_date": "2010-01-01", "snapshot_date": day}

    db_mod.StockSnapshotDAO().save_batch([
        snap("600519", 99.0, "2026-10-20"),
        snap("000001", 12.0, "2026-10-26"),
    ])
    vector = compute_vector("2026-10-26")
    assert vector.quality["snapshot_date"] == "2026-10-26"
    assert vector.metrics["pe_median"] == 12.0
