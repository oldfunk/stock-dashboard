"""批13-e：零价不进价格契约列——模型层丢弃 + DAO 层闸门（2026-11-03，不碰网络）。

产线证据（nec 只读，2026-11-03）：母方把 184 行 BJ 零价的
close/close_raw/close_adj/close_qfq 置成了 NULL，`open/high/low` 仍是 0.0；
全表 `close=0` 已归零，`open=0` 还剩 187 行。**存量修完了，代码侧必须断根**：
否则下一次回填/每日增量又会把 0 写回去。

两层各钉一件事：
- `merge_price_model`：上游把无成交/停牌日写成 0.00 → 这样的输入行整行不产出
  （停牌=无数据，fail-closed），锚点也不会被零价尾日拉成 0。
- `KlineDAO.upsert_many`：价格六列只认 >0；非正/空**不覆盖已有的真价**，
  新行则存 NULL。`volume`/`amount` 的 0 是真实无成交，照写——这条区分是本件
  最容易写错的地方，所以单独有用例守着。
"""

import pytest

from src.collector.price_model import merge_price_model
from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "k.db"))
    db_mod.init_database()
    return db_mod


def _raw(day, close, **kw):
    r = {"trade_date": day, "close": close, "volume": 1000.0, "amount": 1e4}
    r.update(kw)
    return r


def _adj(day, close, **kw):
    r = {"trade_date": day, "open": close, "close": close,
         "high": close * 1.1, "low": close * 0.9}
    r.update(kw)
    return r


# ── 模型层 ──

def test_suspended_zero_close_row_is_not_produced():
    """任一侧 close=0（上游的无成交日写法）→ 那天整行不进模型，相邻正常行不受影响。"""
    raw = [_raw("2024-01-02", 10.0), _raw("2024-01-03", 0.0), _raw("2024-01-04", 9.0)]
    adj = [_adj("2024-01-02", 100.0), _adj("2024-01-03", 0.0), _adj("2024-01-04", 90.0)]
    rows = merge_price_model(raw, adj)
    assert [r["trade_date"] for r in rows] == ["2024-01-02", "2024-01-04"]
    assert all(r["close"] > 0 and r["close_raw"] > 0 and r["close_adj"] > 0
               for r in rows)


def test_zero_on_one_side_alone_is_enough_to_drop_the_day():
    """只有 hfq 侧是 0 也照样丢：0 出现在任何一侧都不是价。"""
    raw = [_raw("2024-01-02", 10.0), _raw("2024-01-03", 10.0)]
    adj = [_adj("2024-01-02", 100.0), _adj("2024-01-03", 0.0)]
    rows = merge_price_model(raw, adj)
    assert [r["trade_date"] for r in rows] == ["2024-01-02"]


def test_negative_close_is_not_a_price_either():
    rows = merge_price_model([_raw("2024-01-02", -1.0)], [_adj("2024-01-02", 100.0)])
    assert rows == []


def test_anchor_falls_back_to_last_real_trading_day():
    """尾日是零价时锚点回落到"最后一个有真成交的交集日"。

    §9.1 要求最新一行恒有 close == close_raw；若 scale 用 0 价那天算，
    整窗口会被缩放成 0（或 NaN），这条把两者都钉住。
    """
    raw = [_raw("2024-01-02", 10.0), _raw("2024-01-03", 12.0), _raw("2024-01-04", 0.0)]
    adj = [_adj("2024-01-02", 100.0), _adj("2024-01-03", 120.0), _adj("2024-01-04", 0.0)]
    rows = merge_price_model(raw, adj)
    assert [r["trade_date"] for r in rows] == ["2024-01-02", "2024-01-03"]
    assert rows[-1]["close"] == rows[-1]["close_raw"] == 12.0
    assert rows[0]["close"] == pytest.approx(10.0)


def test_all_suspended_window_returns_empty_not_crash():
    rows = merge_price_model([_raw("2024-01-02", 0.0)], [_adj("2024-01-02", 0.0)])
    assert rows == []


# ── DAO 层：闸门 ──

def _cell(code="600519", day="2024-01-02", **kw):
    r = {"trade_date": day, "open": 10.0, "close": 10.0, "high": 11.0,
         "low": 9.0, "volume": 0.0, "amount": 0.0, "close_raw": 10.0,
         "close_adj": 100.0}
    r.update(kw)
    return r


def _read(db, code="600519", day="2024-01-02"):
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM kline_daily WHERE code=? AND trade_date=?",
            (code, day)).fetchone()
    return dict(row) if row else None


@pytest.mark.parametrize("col", ["open", "close", "high", "low",
                                "close_raw", "close_adj"])
def test_existing_price_is_never_overwritten_by_zero(db, col):
    """0 不许覆盖已有的真价——下一次回填/增量重跑同一天的场景。"""
    db.KlineDAO().upsert_many("600519", [_cell()])
    db.KlineDAO().upsert_many("600519", [_cell(**{col: 0.0})])
    assert _read(db)[col] == {"open": 10.0, "close": 10.0, "high": 11.0,
                              "low": 9.0, "close_raw": 10.0,
                              "close_adj": 100.0}[col]


def test_explicit_null_close_still_clears_per_p0_1(db):
    """闸门不能把 Hermes P0-1 那条契约顺手动掉：**显式传空**仍清空，
    而 close_raw/close_adj 的"没带字段"（旧调用）继续保留（2026-10-26 契约）。
    三态（>0 覆盖 / <=0 保留 / NULL 清空）就是这个用例和上一个用例的差。
    """
    db.KlineDAO().upsert_many("600519", [_cell()])
    db.KlineDAO().upsert_many("600519", [{"trade_date": "2024-01-02",
                                          "open": None, "close": None,
                                          "high": None, "low": None,
                                          "volume": 1.0, "amount": 1.0,
                                          "turnover": 0.1}])
    row = _read(db)
    assert row["close"] is None and row["close_qfq"] is None
    assert row["open"] is None and row["high"] is None and row["low"] is None
    assert row["close_raw"] == 10.0 and row["close_adj"] == 100.0, \
        "只带 close 的旧调用不许清空 raw/adj（2026-10-26 契约）"


def test_new_row_stores_null_instead_of_zero(db):
    """新行（库里还没有这天）也存 NULL：与母方对 184 行存量的处置同形。"""
    db.KlineDAO().upsert_many("600519", [
        _cell(close=0.0, open=0.0, high=0.0, low=0.0, close_raw=0.0,
              close_adj=0.0, close_qfq=0.0)])
    row = _read(db)
    for col in ("open", "close", "high", "low", "close_raw", "close_adj",
                "close_qfq"):
        assert row[col] is None, f"{col} 存了 0，零价又进契约列了"


def test_zero_volume_and_amount_are_still_written(db):
    """口径的另一半：volume/amount 的 0 是**真实无成交**，不置 NULL、不被保留旧值。"""
    db.KlineDAO().upsert_many("600519", [_cell()])
    db.KlineDAO().upsert_many("600519", [_cell(volume=5.0, amount=50.0)])
    db.KlineDAO().upsert_many("600519", [_cell(volume=0.0, amount=0.0)])
    row = _read(db)
    assert row["volume"] == 0.0 and row["amount"] == 0.0


def test_positive_rewrite_still_works(db):
    """闸门不能把正常重锚挡掉：`refresh_qfq_anchor` 靠 upsert 改 close/close_qfq。"""
    db.KlineDAO().upsert_many("600519", [_cell()])
    db.KlineDAO().upsert_many("600519", [_cell(close=8.0, close_raw=8.0)])
    row = _read(db)
    assert row["close"] == 8.0 and row["close_raw"] == 8.0


def test_close_qfq_mirrors_whatever_close_ends_up_as(db):
    """P0-1 契约在闸门之后仍然成立：传入 0 时 close 保留旧价，qfq 跟着同一个值。"""
    db.KlineDAO().upsert_many("600519", [_cell()])
    db.KlineDAO().upsert_many("600519", [_cell(close=0.0, close_qfq=999.0)])
    row = _read(db)
    assert (row["close"], row["close_qfq"]) == (10.0, 10.0)
    db.KlineDAO().upsert_many("600519", [_cell(close=7.0)])
    row = _read(db)
    assert (row["close"], row["close_qfq"]) == (7.0, 7.0)


def test_model_then_dao_leaves_no_zero_anywhere(db):
    """端到端：停牌日在模型层就被丢掉，DAO 层再兜一道，两层的账对得上。"""
    rows = merge_price_model(
        [_raw("2024-01-02", 10.0), _raw("2024-01-03", 0.0)],
        [_adj("2024-01-02", 100.0), _adj("2024-01-03", 0.0)])
    assert db.KlineDAO().upsert_many("920000", rows) == 1
    with db.db_conn() as conn:
        n_zero = conn.execute(
            "SELECT COUNT(*) FROM kline_daily WHERE close <= 0 OR close_raw <= 0"
            " OR close_adj <= 0 OR close_qfq <= 0").fetchone()[0]
        got = conn.execute(
            "SELECT trade_date, close, close_qfq FROM kline_daily").fetchall()
    assert n_zero == 0
    assert [dict(g) for g in got] == [{"trade_date": "2024-01-02",
                                       "close": 10.0, "close_qfq": 10.0}]
