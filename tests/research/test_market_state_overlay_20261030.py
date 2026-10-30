"""当日收盘覆盖层（F2，2026-10-30）：daily_close 补全市场样本。

场景：kline 当日行只覆盖观察池+Top25（产线实测 21 只/日），而广度/波动率/
流动性需要当日全市场样本——没有覆盖层时 pct_above_ma60 只代表那 21 只，
coverage 却仍是 1.0（假象）。
"""

from datetime import date, timedelta

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "ms.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


D = "2026-10-30"
HIST = [(date(2026, 8, 3) + timedelta(days=i)).isoformat() for i in range(60)]


def _seed_kline(code: str, adj: float, raw: float, dates=HIST, amount=1e6):
    dao = db_mod.KlineDAO()
    dao.upsert_many(code, [{
        "trade_date": d, "open": raw, "close": raw, "high": raw, "low": raw,
        "volume": 100, "amount": amount,
        "close_raw": raw, "close_adj": adj,
    } for d in dates])


def test_breadth_uses_overlay_for_full_market(db):
    """无当日 kline 行的票由覆盖层折算 adj 后入列：样本从 0 → 3。"""
    from src.models.database import DailyCloseDAO
    from src.research.market_state import compute_vector
    for code in ("000001", "000002", "000003"):
        _seed_kline(code, adj=10.0, raw=5.0)          # 比值 2.0，历史平坦
    # raw 收盘：000001/000003 上穿（adj 12/11 > MA60=10），000002 下穿（adj 8）
    DailyCloseDAO().upsert_batch([
        {"code": "000001", "current_price": 6.0},
        {"code": "000002", "price": 4.0},
        {"code": "000003", "current_price": 5.5},
    ], D)

    v = compute_vector(D)
    assert v.metrics["pct_above_ma60"] == pytest.approx(66.6667, abs=0.01)
    assert v.metrics["pct_above_ma20"] == pytest.approx(66.6667, abs=0.01)
    assert v.metrics["adv_dec_ratio"] == 2.0          # 2 涨 1 跌
    assert v.quality["breadth_n"] == 3
    assert v.quality["overlay_n"] == 3
    # 波动率：历史日收益全 0、仅当日由覆盖层产生异动 → 年化 > 0
    # （>0 证明覆盖层的当日收益真的进了收益序列，而不是只进了广度）
    assert v.metrics["volatility_20d"] > 0


def test_kline_day_row_wins_over_overlay(db):
    """kline 已有当日行的 code 不被覆盖层顶替（三列契约为准）。"""
    from src.models.database import DailyCloseDAO
    from src.research.market_state import _load_overlay, _read_txn
    _seed_kline("000001", adj=10.0, raw=5.0)
    _seed_kline("000002", adj=20.0, raw=10.0, dates=HIST + [D])  # 当日已有 kline 行
    DailyCloseDAO().upsert_batch([
        {"code": "000001", "close": 4.0},    # 缺价键名变体：close 直给
        {"code": "000002", "current_price": 3.0},
    ], D)
    with _read_txn() as conn:
        overlay, n = _load_overlay(conn, D)
    assert set(overlay) == {"000001"}
    assert n == 1


def test_liquidity_uses_overlay_amount(db):
    """当日成交额优先取覆盖层全日额；20 日均值里当日那格同样替换。"""
    from src.models.database import DailyCloseDAO
    from src.research.market_state import compute_vector
    _seed_kline("000001", adj=10.0, raw=5.0,
                dates=HIST[:59] + [HIST[-1]], amount=1e6)   # kline 无当日行
    _seed_kline("000002", adj=10.0, raw=5.0,
                dates=HIST, amount=1e6)
    DailyCloseDAO().upsert_batch([
        {"code": "000001", "current_price": 5.0, "amount": 5e8},
        {"code": "000002", "current_price": 5.0, "amount": 7e8},
    ], D)
    v = compute_vector(D)
    # 覆盖层全日额 12 亿；若误用 kline 窄样本会接近 0
    assert v.metrics["total_turnover_yi"] == pytest.approx(12.0)


def test_no_overlay_rows_behavior_unchanged(db):
    """无覆盖层时一切照旧（向后兼容；当日无 kline 行 → 广度样本 0）。"""
    from src.research.market_state import compute_vector
    for code in ("000001", "000002"):
        _seed_kline(code, adj=10.0, raw=5.0)
    v = compute_vector(D)
    assert v.quality["breadth_n"] == 0
    assert v.quality["overlay_n"] == 0
    # None 值不进 metrics（落 missing）；键不存在与值为 None 对消费方等价
    assert v.metrics.get("pct_above_ma60") is None


def test_daily_close_dao_key_variants(db):
    """主路径 current_price / 兜底 price / 直给 close 三种键名都收；closes_for 可读。"""
    dao = db_mod.DailyCloseDAO()
    n = dao.upsert_batch([
        {"code": "000001", "current_price": 10.0, "amount": 1.0, "volume": 2.0},
        {"code": "000002", "price": 11.0},
        {"code": "000003", "close": 12.0},
        {"code": "000004", "current_price": None},   # 无价 → 跳过
        {"name": "无 code"},                          # 无码 → 跳过
    ], D)
    assert n == 3
    got = dao.closes_for(D)
    assert got["000001"]["close"] == 10.0
    assert got["000002"]["close"] == 11.0
    assert got["000003"]["close"] == 12.0
    assert "000004" not in got
