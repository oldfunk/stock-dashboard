"""M2 行情数据质量守卫：缺口/重复/异常涨跌幅/停牌停滞/无数据 + 同步落流水。"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paper_trading.cli as hb  # noqa: E402
from paper_trading.models import Bar  # noqa: E402


def _trading_days(n: int, end=None) -> list[date]:
    """最近 n 个交易日（升序），与守卫同用一个日历判断，测试自洽。"""
    from paper_trading.utils.trading_calendar import is_trading_day

    d = end or date.today()
    out = []
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _bar(sym: str, d: date, close: float, hour: int = 0) -> Bar:
    return Bar(symbol=sym, timestamp=datetime(d.year, d.month, d.day, hour),
               open=close, high=close, low=close, close=close, volume=100)


def _bridge(tmp_path):
    return hb.TradingBridge(data_db=str(tmp_path / "d.db"),
                            account_db=str(tmp_path / "a.db"),
                            config_path="/nonexistent.yaml")


def test_quality_clean_series(tmp_path):
    """连续交易日、价格平稳 → 零发现。"""
    from paper_trading.data.quality import check

    b = _bridge(tmp_path)
    days = _trading_days(6)
    b.data_db.upsert_bars([_bar("600519", d, 10.0) for d in days])
    q = check(b.data_db, ["600519"])
    assert q["clean"] and q["n_findings"] == 0 and q["checked"] == 1


def test_quality_flags_all_defect_types(tmp_path):
    """四类缺陷 + 无数据：全部命中对应 type。"""
    from paper_trading.data.quality import check

    b = _bridge(tmp_path)
    days = _trading_days(6)
    # A：老股（40 天前已有 K 线，不落入新股宽限期）+ 同日重复 + 异常涨跌 + 中段缺口
    a_rows = [_bar("600519", date.today() - timedelta(days=40), 10.0),
              _bar("600519", days[0], 10.0),
              _bar("600519", days[1], 10.0),
              _bar("600519", days[1], 10.0, hour=15),   # 同日第二条（绕过主键）
              _bar("600519", days[3], 16.0),            # +60% 异常涨跌（days[2] 缺口）
              _bar("600519", days[4], 16.0),
              _bar("600519", days[5], 16.0)]
    b.data_db.upsert_bars(a_rows)
    # B：末尾停滞（最后 1 根在 40 个自然日前）
    old_end = date.today() - timedelta(days=40)
    b_days = _trading_days(4, end=old_end)
    b.data_db.upsert_bars([_bar("000001", d, 10.0) for d in b_days])
    # C：无数据；D：干净
    c_days = _trading_days(6)
    b.data_db.upsert_bars([_bar("600036", d, 40.0) for d in c_days])

    q = check(b.data_db, ["600519", "000001", "601398", "600036"])
    assert not q["clean"] and q["checked"] == 4
    types_a = {f["type"] for f in q["findings"].get("600519", [])}
    assert "duplicate_date" in types_a
    assert "absurd_move" in types_a
    assert "gap" in types_a          # days[2] 被跳过
    types_b = {f["type"] for f in q["findings"].get("000001", [])}
    assert "stale" in types_b        # 40 自然日 ≈ 28 交易日 ≥ 10
    assert "no_data" in {f["type"] for f in q["findings"].get("601398", [])}
    assert "600036" not in q["findings"]   # 干净标的不出现


def test_quality_bad_price_flagged(tmp_path):
    from paper_trading.data.quality import check_symbol

    b = _bridge(tmp_path)
    days = _trading_days(3)
    b.data_db.upsert_bars([_bar("600519", days[0], 10.0),
                           _bar("600519", days[1], 0.0),
                           _bar("600519", days[2], 10.0)])
    f = check_symbol(b.data_db, "600519")
    assert any(x["type"] == "bad_price" for x in f)


def test_quality_new_listing_grace(tmp_path):
    """M-3：上市首周的大幅波动是合法行情（创业板前 5 日无涨跌幅），不判脏。"""
    from paper_trading.data.quality import check_symbol

    b = _bridge(tmp_path)
    d0, d1, d2 = _trading_days(3)
    b.data_db.upsert_bars([_bar("301001", d0, 10.0),
                           _bar("301001", d1, 30.0),   # +200%：新股合法
                           _bar("301001", d2, 28.0)])
    f = check_symbol(b.data_db, "301001")
    assert not any(x["type"] == "absurd_move" for x in f), f
    # 老股同样幅度仍然要报（40 天前有 K 线 = 不在宽限期）
    old_days = _trading_days(2)
    b.data_db.upsert_bars([_bar("600001", date.today() - timedelta(days=40), 10.0),
                           _bar("600001", old_days[0], 10.0),
                           _bar("600001", old_days[1], 30.0)])
    f2 = check_symbol(b.data_db, "600001")
    assert any(x["type"] == "absurd_move" for x in f2), f2


def test_sync_quality_oplog_once_per_day(monkeypatch, tmp_path):
    """M-2：同日多次 sync 只记一条 data:quality（停牌/停滞不许刷屏 op_log）。"""
    from paper_trading.models import Bar as _Bar

    class FakeFetcher:
        @staticmethod
        def fetch_daily(symbol, start_date=None, **kw):
            return [_Bar(symbol=symbol,
                         timestamp=datetime(2024, 1, 1) + timedelta(days=i),
                         open=10.0, high=11.0, low=9.0, close=10.0, volume=100)
                    for i in range(5)]

        @staticmethod
        def fetch_stock_names(symbols):
            return {}

    monkeypatch.setattr(hb, "_get_fetcher", lambda: FakeFetcher)
    b = _bridge(tmp_path)
    b.sync_data(["600519"])
    b.sync_data(["600519"])          # 同日第二次（模拟 cron 多轮）
    ops = [o for o in b.broker.get_op_log(30) if o["action"] == "data:quality"]
    assert len(ops) == 1, f"同日多次 sync 只应记 1 条，实际 {len(ops)}"


def test_sync_data_logs_quality_findings(monkeypatch, tmp_path):
    """sync_data 挂钩：有发现落 op_log[data:quality]（不静默），无发现不落。"""
    from paper_trading.models import Bar as _Bar

    class FakeFetcher:
        @staticmethod
        def fetch_daily(symbol, start_date=None, **kw):
            # 2024 年的旧数据：同步"成功"但相对今天已停滞 → stale 必中
            return [_Bar(symbol=symbol,
                         timestamp=datetime(2024, 1, 1) + timedelta(days=i),
                         open=10.0, high=11.0, low=9.0, close=10.0, volume=100)
                    for i in range(5)]

        @staticmethod
        def fetch_stock_names(symbols):
            return {}

    monkeypatch.setattr(hb, "_get_fetcher", lambda: FakeFetcher)
    b = _bridge(tmp_path)
    res = b.sync_data(["600519"])
    assert res["ok"]
    assert "quality" in res and not res["quality"]["clean"]
    ops = [o for o in b.broker.get_op_log(30) if o["action"] == "data:quality"]
    assert ops, "质量发现必须落 op_log（同步失败/停滞不可静默）"


def test_quality_cli_dispatch(tmp_path, capsys):
    """CLI `data quality` 可用且输出结构含 findings 键（离线、临时库）。"""
    b = _bridge(tmp_path)
    days = _trading_days(3)
    b.data_db.upsert_bars([_bar("600519", d, 10.0) for d in days])
    import json
    import subprocess

    out = subprocess.run(
        [sys.executable, "-m", "paper_trading.cli", "--data-db",
         str(tmp_path / "d.db"), "--account-db", str(tmp_path / "a.db"),
         "data", "quality", "--symbols", "600519", "601398", "--json"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    data = json.loads(out.stdout)
    assert data["ok"] and "findings" in data["data"]
    # 601398 无数据 → 有发现；600519 干净
    assert "601398" in data["data"]["findings"]
