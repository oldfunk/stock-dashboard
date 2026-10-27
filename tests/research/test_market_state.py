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


def test_kline_trade_date_index(db):
    """市场状态快照按 trade_date 取近窗口，依赖此索引避免全表扫描。"""
    with db_mod.db_conn() as conn:
        idx = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='kline_daily'")}
    assert "idx_kline_trade_date" in idx


def test_compute_vector_pit_per_code_latest_snapshot(db):
    """审计 F6：PIT 按 code 取 ≤ trade_date 的最新行（样本=全量可用行），
    不再只取全局 MAX 单日桶（旧语义：历史日只剩残余桶十几行）。"""
    from src.research.market_state import compute_vector

    def snap(code, pe, day):
        return {"code": code, "name": "X", "market": "A", "sector": "s", "pe": pe,
                "pb": 1.0, "ps": 1.0, "market_cap": 1.0, "circulating_cap": 1.0,
                "roe": 10.0, "revenue": 1.0, "revenue_growth": 5.0,
                "profit": 1.0, "profit_growth": 5.0, "debt_ratio": 30.0,
                "current_price": 1.0, "high_52w": 1.0, "low_52w": 1.0,
                "is_st": False, "list_date": "2010-01-01", "snapshot_date": day}

    # 三行：10-20 残余行、10-26 新行、10-27 前视行（必须被排除）
    db_mod.StockSnapshotDAO().save_batch([
        snap("600519", 99.0, "2026-10-20"),
        snap("000001", 12.0, "2026-10-26"),
        snap("300750", 5.0, "2026-10-27"),
    ])
    vector = compute_vector("2026-10-26")
    # 旧语义只取 10-26 桶 → 12.0；PIT 语义纳入 10-20 残余行 → median(99,12)
    assert vector.metrics["pe_median"] == 55.5
    assert vector.quality["snapshot_date"] == "2026-10-26"   # 样本内最新刷新日
    assert vector.quality["snapshot_n"] == 2                  # 前视行被排除
    assert "300750" not in str(vector.quality)                # 10-27 行不参与


def test_volatility_immune_to_coverage_composition_shift(db):
    """审计 F7：覆盖组成切换（如回填日 958 只 → 增量日 261 只）不得造伪波动。

    旧实现对"每日 AVG(close_adj)"取收益：均价从 (10+1000)/2 跳到 10 会产出
    ~98% 的伪日收益；逐票日收益下，价格未变的票收益为 0 → vol = 0。
    """
    from src.research.market_state import compute_vector
    days = ["2026-10-22", "2026-10-23", "2026-10-26"]
    dao = db_mod.KlineDAO()
    # 稳定票：三天都在，价格不变
    for d in days:
        dao.upsert_many("600519", [{
            "trade_date": d, "open": 10.0, "close": 10.0, "high": 10.0,
            "low": 10.0, "volume": 1.0, "amount": 1e8, "turnover": 0.1,
            "close_adj": 10.0}])
    # 组成切换票：只有第一天，价格畸高（旧实现会把它算进当日均价）
    dao.upsert_many("000858", [{
        "trade_date": days[0], "open": 1000.0, "close": 1000.0, "high": 1000.0,
        "low": 1000.0, "volume": 1.0, "amount": 1e8, "turnover": 0.1,
        "close_adj": 1000.0}])
    vector = compute_vector(days[-1])
    assert "volatility_20d" in vector.metrics, "波动率字段应算出"
    assert vector.metrics["volatility_20d"] == 0.0, (
        f"组成切换不得造伪波动，得到 {vector.metrics['volatility_20d']}")
    # 覆盖样本量入 quality，供审计追溯
    assert vector.quality["kline_codes"] == 1   # 当日只有 600519 有行
    assert vector.quality["breadth_n"] == 0     # 无票满足 60 日窗


def test_gross_margin_pit_no_lookahead(db):
    """审计 Hermes P0-2：毛利率中位数只用 trade_date 前已披露的年报。

    seed 两只票：
    - 600519：2025 年报 notice=2026-03-20（trade_date 前已披露）gm=70
    - 000001：2025 年报 notice=2026-11-30（trade_date **后**才披露）gm=10
      同时有 2024 年报 notice=2025-04-10 gm=50（应被采用）
    trade_date=2026-10-26 时，正确 PIT 取 70 与 50 → median=60；
    若前视（取各自最新年报）会得 median(70,10)=40。
    """
    from src.research.market_state import compute_vector

    def fh(code, report_date, gm, notice):
        with db_mod.db_conn() as conn:
            conn.execute(
                "INSERT INTO financial_history (stock_code, report_date, "
                "report_type, gross_margin, notice_date, created_at) "
                "VALUES (?, ?, 'A', ?, ?, '2026-10-27')",
                (code, report_date, gm, notice))

    fh("600519", "2025-12-31", 70.0, "2026-03-20")
    fh("000001", "2025-12-31", 10.0, "2026-11-30")   # 未来披露 -> 排除
    fh("000001", "2024-12-31", 50.0, "2025-04-10")   # PIT 最新已披露
    vector = compute_vector("2026-10-26")
    assert vector.metrics["gross_margin_median"] == 60.0


def test_gross_margin_excluded_without_notice_date(db):
    """仓内口径：缺 notice_date 的年报不参与（不回退 report_date，防前视）。"""
    from src.research.market_state import compute_vector
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT INTO financial_history (stock_code, report_date, "
            "report_type, gross_margin, notice_date, created_at) "
            "VALUES ('600519', '2025-12-31', 'A', 70.0, NULL, '2026-10-27')")
    vector = compute_vector("2026-10-26")
    assert "gross_margin_median" in vector.missing


def test_volatility_and_regime(db):
    from datetime import date, timedelta

    from src.research.market_state import compute_vector
    from src.research.regime import classify_regime
    day = date(2026, 8, 3)
    days = []
    while len(days) < 65:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    dao = db_mod.KlineDAO()
    for d in days:
        dao.upsert_many("600519", [{
            "trade_date": d, "open": 1.0, "close": 1.0, "high": 1.0,
            "low": 1.0, "volume": 1.0, "amount": 1e8, "turnover": 0.1,
            "close_adj": 1.0}])
    vector = compute_vector(days[-1])
    assert "volatility_20d" not in vector.missing
    assert classify_regime(vector).regime != "unknown"
