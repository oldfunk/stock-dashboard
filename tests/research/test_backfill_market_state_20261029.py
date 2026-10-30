"""`scripts/backfill_market_state.py` 回归测试（批9-a，2026-10-29）。

守的是脚本对外承诺的五件事：
1. **窗口口径**：`--days N` = kline_daily 里最近 N 个**有行情行的**交易日；
2. **幂等**：同一日重算覆盖，不追加重复行；
3. **续传**：以目标表已有行为进度——中断后重启只补剩下的日子，不白算；
4. **代价与出口**：源数据被修正后旧行不自动失效，`--force` 才重算；
5. **零写入面**：`--dry-run` 两张表都不碰；异常外抛不静默跳日。

单日取数的"同事务自洽"（P2-3）由 `tests/research/test_market_state.py` 的
三条用例守，本文件不重复；这里守的是**跨日**那一半：窗口开跑即冻结。
"""

import json

import pytest

from src.models import database as db_mod

DAY = "2026-10-26"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "bf.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _weekdays(n, start="2026-08-03"):
    from datetime import date, timedelta
    day = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _seed_kline(days, code="600519", amount=1e8):
    dao = db_mod.KlineDAO()
    for d in days:
        dao.upsert_many(code, [{
            "trade_date": d, "open": 1.0, "close": 1.0, "high": 1.0,
            "low": 1.0, "volume": 1.0, "amount": amount, "turnover": 0.1,
            "close_adj": 1.0}])


def _count(table):
    with db_mod.db_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _state_dates():
    with db_mod.db_conn() as conn:
        return [r["trade_date"] for r in conn.execute(
            "SELECT trade_date FROM market_state_daily ORDER BY trade_date")]


# ── 1. 窗口口径 ────────────────────────────────────────────────────────────

def test_recent_window_uses_latest_kline_dates(db):
    from src.research.market_state import recent_trading_dates
    days = _weekdays(8)
    _seed_kline(days)
    assert recent_trading_dates(3) == days[-3:]                 # 缺省右端=最新交易日
    assert recent_trading_dates(3, days[4]) == days[2:5]        # 显式 --end 左移窗口
    assert recent_trading_dates(99) == days                     # 窗口大于存量即全取


def test_recent_window_ignores_dates_without_kline(db):
    """日历里有、行情里没有的交易日不进回填窗口（那种日子只会写出 NULL 向量）。"""
    from src.research.market_state import recent_trading_dates
    days = _weekdays(5)
    _seed_kline(days)
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO trading_calendar (market_id, trade_date, is_open)"
                     " VALUES ('CN', '2026-12-31', 1)")
    window = recent_trading_dates(10)
    assert window == days
    assert "2026-12-31" not in window


def test_recent_window_empty_db_returns_empty(db):
    from src.research.market_state import recent_trading_dates
    assert recent_trading_dates(60) == []


def test_run_passes_end_through_to_the_window(db):
    """`--end` 必须真的进窗口：只测 src 侧函数测不出脚本少传了参数。"""
    from scripts.backfill_market_state import run
    days = _weekdays(65)
    _seed_kline(days)
    result = run(days=2, end=days[9])
    assert result["window_start"] == days[8] and result["window_end"] == days[9]
    assert _state_dates() == days[8:10]


# ── 2/3/4. 写入、幂等、续传、--force ───────────────────────────────────────

def test_backfill_writes_window_and_quality(db):
    from scripts.backfill_market_state import run
    days = _weekdays(65)
    _seed_kline(days)
    result = run(days=5)
    assert result["computed"] == 5 and result["skipped"] == 0
    assert result["window_start"] == days[-5] and result["window_end"] == days[-1]
    assert sum(result["status"].values()) == 5          # 每天都归入某个状态
    assert _state_dates() == days[-5:]
    # 质量记录同步落库：R0-5 质量门按 dataset 读它，回填日不能凭空缺席
    assert _count("data_quality_daily") == 5


def test_backfill_is_idempotent_no_duplicate_rows(db):
    from scripts.backfill_market_state import run
    from src.research.market_state import get_vector
    days = _weekdays(65)
    _seed_kline(days)
    run(days=3, force=True)
    metrics = {d: get_vector(d).metrics for d in days[-3:]}
    run(days=3, force=True)          # 重算覆盖，不追加
    assert _count("market_state_daily") == 3
    assert {d: get_vector(d).metrics for d in days[-3:]} == metrics


def test_backfill_resumes_from_target_table(db):
    """续传不靠进度表：已有行的日期即"已完成"，重跑只报 skipped。"""
    from scripts.backfill_market_state import run
    days = _weekdays(65)
    _seed_kline(days)
    run(days=5)
    again = run(days=5)
    assert again["computed"] == 0 and again["skipped"] == 5
    assert _count("market_state_daily") == 5


def test_backfill_survives_mid_run_crash(db, monkeypatch):
    """崩在第 3 天：前 2 天已落库，重启后只补剩下的 3 天（这才叫断点续传）。"""
    import scripts.backfill_market_state as script
    from src.research.market_state import snapshot_day as real_snapshot

    calls = []

    def crash_on_third(day, dry_run=False, dao=None):
        calls.append(day)
        if len(calls) == 3:
            raise RuntimeError("模拟中途崩溃")
        return real_snapshot(day, dry_run=dry_run, dao=dao)

    days = _weekdays(65)
    _seed_kline(days)
    window = days[-5:]
    monkeypatch.setattr(script, "snapshot_day", crash_on_third)
    with pytest.raises(RuntimeError):
        script.run(days=5)
    assert _state_dates() == window[:2]        # 崩之前的两天已落库

    monkeypatch.setattr(script, "snapshot_day", real_snapshot)
    result = script.run(days=5)
    assert result["computed"] == 3 and result["skipped"] == 2
    assert _count("market_state_daily") == 5


def test_source_correction_needs_force(db):
    """如实钉住代价：源表被修正后旧行不自动失效，`--force` 才重算。"""
    from scripts.backfill_market_state import run
    from src.research.market_state import get_vector
    days = _weekdays(65)
    _seed_kline(days)

    def snap(code, pe):
        return {"code": code, "name": "X", "market": "A", "sector": "s", "pe": pe,
                "pb": 1.0, "ps": 1.0, "market_cap": 1.0, "circulating_cap": 1.0,
                "roe": 10.0, "revenue": 1.0, "revenue_growth": 5.0,
                "profit": 1.0, "profit_growth": 5.0, "debt_ratio": 30.0,
                "current_price": 1.0, "high_52w": 1.0, "low_52w": 1.0,
                "is_st": False, "list_date": "2010-01-01", "snapshot_date": days[-1]}

    db_mod.StockSnapshotDAO().save_batch([snap("600519", 20.0)])
    run(days=1)
    assert get_vector(days[-1]).metrics["pe_median"] == 20.0

    db_mod.StockSnapshotDAO().save_batch([snap("600519", 100.0)])   # 源数据被修正
    assert run(days=1)["computed"] == 0                             # 默认跳过（代价）
    assert get_vector(days[-1]).metrics["pe_median"] == 20.0
    assert run(days=1, force=True)["computed"] == 1                 # --force 出口
    assert get_vector(days[-1]).metrics["pe_median"] == 100.0


# ── 5. 写入面与跨日窗口 ────────────────────────────────────────────────────

def test_dry_run_touches_neither_target_table(db):
    from scripts.backfill_market_state import run
    days = _weekdays(65)
    _seed_kline(days)
    result = run(days=4, dry_run=True)
    assert result["computed"] == 4 and result["dry_run"] is True
    assert _count("market_state_daily") == 0
    assert _count("data_quality_daily") == 0
    # dry-run 不虚报续传：第二次仍然把这 4 天当作待办
    assert run(days=4, dry_run=True)["skipped"] == 0


def test_window_is_frozen_while_new_dates_arrive(db, monkeypatch):
    """跨日那一半的自洽：跑到中途来了更新交易日，窗口也不滑（否则永远补不完）。"""
    import scripts.backfill_market_state as script
    from src.research.market_state import snapshot_day as real_snapshot

    days = _weekdays(65)
    _seed_kline(days)
    seen = {"once": False}

    def snapshot_then_insert(day, dry_run=False, dao=None):
        vector = real_snapshot(day, dry_run=dry_run, dao=dao)
        if not seen["once"]:
            seen["once"] = True
            _seed_kline(["2099-01-02"])       # 模拟并发行情写入库内最新交易日
        return vector

    monkeypatch.setattr(script, "snapshot_day", snapshot_then_insert)
    result = script.run(days=5)
    assert result["computed"] == 5
    assert result["window_end"] == days[-1]
    assert "2099-01-02" not in _state_dates()


def test_source_tables_stay_read_only(db):
    """回填不动源表：kline_daily 的行数与金额合计前后一致（写面只有两张目标表）。"""
    from scripts.backfill_market_state import run
    days = _weekdays(65)
    _seed_kline(days)
    with db_mod.db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, SUM(amount) AS s FROM kline_daily").fetchone()
    before = (row["n"], row["s"])
    run(days=3, force=True)
    with db_mod.db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, SUM(amount) AS s FROM kline_daily").fetchone()
    assert (row["n"], row["s"]) == before


# ── CLI ────────────────────────────────────────────────────────────────────

def test_cli_prints_summary_and_exits_zero(db, monkeypatch, capsys):
    from scripts.backfill_market_state import main
    _seed_kline(_weekdays(65))
    assert main(["--days", "2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["computed"] == 2 and payload["window"] == 2


def test_cli_fails_loudly_when_no_kline(db, capsys):
    from scripts.backfill_market_state import main
    assert main(["--days", "60"]) == 1
    assert "kline_daily" in capsys.readouterr().out   # 空窗口的原因要说清


def test_cli_rejects_non_positive_days(db, capsys):
    from scripts.backfill_market_state import main
    assert main(["--days", "0"]) == 2
    assert "正整数" in capsys.readouterr().err


def test_script_module_has_no_chdir():
    """批11 的前置：新脚本只做 sys.path.insert，不改工作目录、不自己连库。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2]
           / "scripts" / "backfill_market_state.py").read_text(encoding="utf-8")
    assert "os.chdir" not in src
    assert "sqlite3" not in src      # 取数/落库全走 src 侧，不留第二个写入口径


def test_vector_written_by_backfill_matches_recompute(db):
    """落库的向量与当场重算一致（序列化不丢字段，regime 已写入）。"""
    from scripts.backfill_market_state import run
    from src.research.market_state import compute_vector, get_vector
    days = _weekdays(65)
    _seed_kline(days)
    run(days=1, force=True)
    stored = get_vector(days[-1])
    fresh = compute_vector(days[-1])
    assert stored.metrics == fresh.metrics
    assert stored.coverage == fresh.coverage
    assert stored.regime != ""       # snapshot_day 补了 regime，纯算没有
