"""批13-d：Sina 日线对北交所是**能力边界**，不是"没数据"（2026-11-03，不打网络）。

产线取证（2026-10-31，nec，交付本体）：`fetch_kline_sina_adjusted("920305", …)`
三次退避后 `RuntimeError: Sina 价格抓取失败: No value to decode`，底层
`_sina_daily` 是 `JSONDecodeError`——akshare 的 `stock_zh_a_daily` 对 bj 前缀给空壳。
旧行为把它记成 partial（每轮白等 5/10/20s），BJ 一旦进目标集就是永久重试噪声。
本文件钉三件事：抛在打网络**之前**、调用方按 skip 记账、**不写进度表**
（记 no_data 会被断点续传永久排除，等于自己堵死"换源重跑"这条路）。
"""

import json

import pytest

from src.models import database as db_mod
from src.collector.akshare_fetcher import SourceUnsupportedError


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "d.db"))
    db_mod.init_database()
    return db_mod


def _annual(code):
    db_mod.FinancialHistoryDAO().batch_save([{
        "stock_code": code, "report_date": "2025-12-31", "roe": 10.0,
    }])


def _no_network(monkeypatch):
    """底层取数一旦被调用就红：证明守卫在 akshare/sleep 之前就抛出。"""
    from scripts import backfill_price_history as s
    calls = []
    monkeypatch.setattr(s, "_sina_daily",
                        lambda *a, **k: calls.append(a) or [])
    sleeps = []
    monkeypatch.setattr(s.time, "sleep", lambda x: sleeps.append(x))
    return s, calls, sleeps


def test_bj_raises_capability_boundary_before_any_network(monkeypatch):
    s, calls, sleeps = _no_network(monkeypatch)
    with pytest.raises(SourceUnsupportedError) as ei:
        s.fetch_kline_sina_adjusted("920305", "2016-01-01")
    assert "北交所" in str(ei.value)
    assert calls == [], "能力边界必须抛在取数之前，不能白打一次 akshare"
    assert sleeps == [], "不许消耗 5/10/20s 退避（321 只 BJ ≈ 每轮 3 小时空等）"


def test_sh_and_sz_still_pull_usually(monkeypatch):
    """守卫只挡 BJ：沪深仍走原路（两拉 + 三列模型），不能顺手把老逻辑改坏。"""
    s, calls, _ = _no_network(monkeypatch)

    def fake(code, adjust, start_date, end_date=None):
        calls.append((code, adjust))
        if adjust == "":
            return [{"trade_date": "2020-01-02", "close": 10.0, "volume": 1},
                    {"trade_date": "2020-01-03", "close": 11.0, "volume": 2}]
        return [{"trade_date": "2020-01-02", "open": 100.0, "close": 100.0,
                 "high": 110.0, "low": 90.0},
                {"trade_date": "2020-01-03", "open": 110.0, "close": 110.0,
                 "high": 120.0, "low": 100.0}]

    monkeypatch.setattr(s, "_sina_daily", fake)
    rows = s.fetch_kline_sina_adjusted("600519", "2020-01-01")
    assert [r["close"] for r in rows] == [10.0, 11.0]
    assert [c[1] for c in calls] == ["", "hfq"], "不复权 + 后复权两拉不变"


@pytest.mark.parametrize("code", ["920305", "830799", "871981", "430139"])
def test_all_four_bj_number_ranges_are_caught(monkeypatch, code):
    """BJ 号段是四个前缀（920/83/87/43），漏一个就还是每轮白跑。"""
    s, calls, sleeps = _no_network(monkeypatch)
    with pytest.raises(SourceUnsupportedError):
        s.fetch_kline_sina_adjusted(code, "2016-01-01")
    assert calls == [] and sleeps == []


def test_price_history_loop_skips_bj_without_touching_progress(db, monkeypatch,
                                                               capsys):
    """主循环侧：BJ 记 skip（含 skip_reasons），进度表一行都不写。

    `no_data` 会被 `pending_codes()` 默认排除、`partial` 每轮重跑——两者都不是
    "这个源供不了"的记账，所以断言的是**进度表为空**，不是某个状态。
    """
    from scripts import backfill_price_history as s
    _annual("920305")
    _annual("830799")
    monkeypatch.setattr("sys.argv", ["backfill", "--all", "--sleep", "0",
                                     "--source", "sina"])
    assert s.main() == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["targets"] == 2
    assert (summary["done"], summary["no_data"], summary["partial"]) == (0, 0, 0)
    assert summary["skipped"] == 2
    assert summary["skip_reasons"] == {"sina_unsupported_bj": 2}
    with db.db_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM backfill_progress WHERE task='price_history'"
        ).fetchone()[0] == 0, "结构性跳过不写进度"
        assert conn.execute(
            "SELECT COUNT(*) FROM kline_daily").fetchone()[0] == 0

    # 不写进度的后果：下一轮仍然会再试（换 --source akshare 就能真补）
    monkeypatch.setattr("sys.argv", ["backfill", "--all", "--sleep", "0"])
    assert s.main() == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])[
        "targets"] == 2


def test_bj_skip_does_not_feed_the_circuit_breaker(db, monkeypatch, capsys):
    """连续失败熔断数的是"上游抽风"，结构性跳过不是失败：5 只 BJ + 阈值 2 也不该中止。"""
    from scripts import backfill_price_history as s
    for code in ("920000", "920001", "920002", "920003", "920004"):
        _annual(code)
    monkeypatch.setattr("sys.argv", ["backfill", "--all", "--sleep", "0",
                                     "--max-consecutive-failures", "2"])
    assert s.main() == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["aborted"] is False
    assert summary["skipped"] == 5 and summary["partial"] == 0


def test_price_depth_counts_bj_as_unsupported_not_progress(db, monkeypatch):
    """深度回填的目标集来自"已有 kline 的票"，BJ 已被 akshare 源回填过 → 会进目标集。

    那里同样不许记 no_data/partial：等 BJ 早段源接上，这些票还得能进来。
    """
    from scripts import backfill_price_depth as d
    with db.db_conn() as conn:
        for day, px in (("2016-01-04", 1), ("2016-01-05", 2)):
            conn.execute(
                "INSERT INTO kline_daily (code, trade_date, open, close, high,"
                " low, volume, amount, close_raw, close_adj, close_qfq)"
                " VALUES ('920305', ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)",
                (day, px, px, px, px, px, px, px))
    calls = []
    monkeypatch.setattr(d, "fetch_kline_sina_adjusted",
                         lambda *a, **k: calls.append(a) or _raise_bj())
    summary = d.run(target_start="2005-01-01", sleep=0.0)
    assert summary["skipped_unsupported"] == 1
    assert (summary["done"], summary["no_data"], summary["partial"]) == (0, 0, 0)
    assert summary["rows_added"] == 0
    with db.db_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM backfill_progress WHERE task='price_depth'"
        ).fetchone()[0] == 0, "结构性跳过不写进度"


def _raise_bj():
    raise SourceUnsupportedError("Sina 日线不支持北交所代码（测试桩）")
