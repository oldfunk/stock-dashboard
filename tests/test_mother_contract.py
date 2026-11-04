"""母库富化契约：逐接口降级 + 来源标注（合成母库，离线可跑；真母库存在时另做列校验）。"""
import sqlite3
import sys
from pathlib import Path

from datetime import date, timedelta

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.integration import mother  # noqa: E402
from paper_trading.integration.settings import mother_db_path, mother_dir  # noqa: E402
from paper_trading.integration.sources import (SRC_LOCAL_AKSHARE,  # noqa: E402
                                               SRC_UNAVAILABLE)

# ---------------------------------------------------------------- 相对日期（T53 根治）
# 两次 flaky 教训：① fixture 写死 2026-10-26 而产品用墙钟判新鲜度 ⇒ 过几天 lag
# 跨过阈值就翻红；② `today+10` 正好落周六 ⇒ 交易日语义的断言随机失效。
# ⇒ 日期一律由"真实今天"倒推，并且**必须是工作日**；只有明确的历史场景
#   （分红报告期 2025-12-31、无重叠对照 2026-09-01）允许写死绝对值。
def _weekday(offset_days: int) -> date:
    """今天偏移若干天后，回退到最近的工作日（周六/周日往前挪）。"""
    d = date.today() + timedelta(days=offset_days)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


D_LATEST = _weekday(0).isoformat()     # 母库"最新一日"（今天或上一个工作日）
D_PREV = _weekday(-1).isoformat()      # 母库"前一日"
D_NEXT = _weekday(1).isoformat()       # 交易日历里的"下一排期"
D_SEQ = [_weekday(-3 - i).isoformat() for i in range(3)][::-1]   # 本地三日升序


REQUIRED = {
    "kline_daily": {"code", "trade_date", "close"},
    "financial_summary": {"stock_code"},
    "dividend_annual": {"stock_code"},
    "benchmark_daily": {"code", "trade_date", "close"},
    "news_item": {"code", "title"},
    "screening_result": {"run_id", "run_date"},
    "watchlist_thesis": {"code"},
    "analysis_outcome": {"stock_code"},
    "market_index": {"index_code", "date"},
    "instrument": {"symbol", "instrument_id"},
    "trading_calendar": {"market_id", "trade_date", "is_open"},
}


def _mk_sd(root: Path) -> Path:
    """造一个最小可用的母库目录（含 src/ 与 data/db/stock_dashboard.db）。"""
    sd = root / "stock-dashboard"
    (sd / "src").mkdir(parents=True, exist_ok=True)
    dbdir = sd / "data" / "db"
    dbdir.mkdir(parents=True, exist_ok=True)
    db = dbdir / "stock_dashboard.db"
    c = sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE kline_daily (code TEXT, trade_date TEXT, open REAL, high REAL,
        low REAL, close REAL, volume REAL, amount REAL, close_raw REAL,
        close_adj REAL, turnover REAL, shares REAL, close_qfq REAL);
    CREATE TABLE financial_summary (stock_code TEXT PRIMARY KEY, roe_5y_avg REAL,
        roe_10y_avg REAL, updated_at TEXT);
    CREATE TABLE dividend_annual (stock_code TEXT, report_date TEXT,
        cash_per_share REAL, eps REAL, reported_yield REAL, source TEXT);
    CREATE TABLE benchmark_daily (code TEXT, trade_date TEXT, close REAL);
    CREATE TABLE news_item (id INTEGER PRIMARY KEY, code TEXT, name TEXT, title TEXT,
        summary TEXT, url TEXT, source TEXT, published_at TEXT, fetched_at TEXT,
        sentiment REAL, ai_brief TEXT, summarized_at TEXT);
    CREATE TABLE screening_result (id INTEGER PRIMARY KEY, run_id TEXT, run_date TEXT,
        code TEXT, name TEXT, score REAL, pe REAL, pb REAL, roe REAL, reason TEXT,
        strategy_tags TEXT, dividend_yield REAL, score_detail TEXT, status TEXT);
    CREATE TABLE watchlist_thesis (id INTEGER PRIMARY KEY, code TEXT UNIQUE,
        core_thesis TEXT, assumptions TEXT, red_lines TEXT, sell_conditions TEXT);
    CREATE TABLE analysis_outcome (analysis_id INTEGER PRIMARY KEY, stock_code TEXT,
        analysis_date TEXT, signal TEXT, confidence REAL, verdict TEXT,
        entry_price REAL, ret_20 REAL, ret_60 REAL, excess_20 REAL, excess_60 REAL);
    CREATE TABLE market_index (id INTEGER PRIMARY KEY, index_code TEXT,
        index_name TEXT, current_value REAL, change_percent REAL, pe REAL, pb REAL,
        date TEXT);
    CREATE TABLE instrument (instrument_id TEXT, market_id TEXT, symbol TEXT,
        exchange TEXT, name TEXT, status TEXT);
    CREATE TABLE trading_calendar (market_id TEXT, trade_date TEXT, is_open INTEGER,
        session_json TEXT, source TEXT);
    """)
    c.execute("""INSERT INTO kline_daily VALUES
        ('600519','%s',1500,1510,1490,1505,1000000,1500000000,1505,1505,0.1,1000000000,1505),
        ('600519','%s',1490,1500,1480,1495,900000,1300000000,1495,1495,0.1,1000000000,1495),
        ('300750','%s',200,210,190,205,500000,100000000,205,1200,0.1,500000000,NULL),
        ('300750','%s',190,200,180,195,400000,80000000,195,1150,0.1,500000000,NULL)""" % (D_LATEST, D_PREV, D_LATEST, D_PREV))
    c.execute("INSERT INTO financial_summary VALUES ('600519', 20.5, 18.2, '%sT22:00:00')" % D_PREV)
    c.execute("INSERT INTO dividend_annual VALUES ('600519','2025-12-31', 3.0, 6.5, 2.0, 'em')")
    c.execute("INSERT INTO benchmark_daily VALUES ('000001','%s', 11000)" % D_LATEST)
    c.execute("""INSERT INTO news_item (code,name,title,summary,source,published_at,sentiment,ai_brief)
        VALUES ('600519','贵州茅台','测试标题','测试摘要','akshare_em','%s 09:00',0.2,'测试简报')""" % D_LATEST)
    c.execute("""INSERT INTO screening_result (run_id,run_date,code,name,score,pe,pb,roe,reason,strategy_tags,dividend_yield,score_detail,status)
        VALUES ('r1','%s','600519','贵州茅台',90.0,20.0,5.0,20.0,'测试理由','value',2.0,'{"total":90}','active')""" % D_PREV)
    c.execute("INSERT INTO watchlist_thesis (code,core_thesis,sell_conditions) VALUES ('600519','测试论点','跌破 1400 卖出')")
    c.execute("""INSERT INTO analysis_outcome (stock_code,analysis_date,signal,confidence,verdict,entry_price)
        VALUES ('600519','%s','buy',0.8,'通过','1495')""" % D_PREV)
    c.execute("INSERT INTO market_index (index_code,index_name,current_value,change_percent,pe,pb,date) VALUES ('000001','上证指数',3300,0.5,13.0,1.3,'%s')" % D_LATEST)
    c.execute("INSERT INTO instrument VALUES ('CN:600519','CN','600519','SSE','贵州茅台','active')")
    c.execute("INSERT INTO trading_calendar VALUES ('CN','%s',1,'{}','test')" % D_NEXT)
    c.execute("INSERT INTO trading_calendar VALUES ('CN','%s',1,'{}','test')" % D_LATEST)
    c.commit()
    c.close()
    return sd


@pytest.fixture()
def sd(tmp_path):
    mother.reset_cache()
    yield _mk_sd(tmp_path)
    mother.reset_cache()


def _local_bar(code, days):
    # 真实量级：同日 qfq 双源差 <0.01%（尺度闸按此假设对照）
    return [{"timestamp": f"{D_SEQ[i - 1]}T00:00:00", "open": 1490, "high": 1500,
             "low": 1485, "close": 1490 + i, "volume": 1000}
            for i in range(1, 4)]


def test_block_marks_stale_by_asof():
    from paper_trading.integration.sources import block

    old = block({"x": 1}, "mother:t", asof="2020-01-01", max_age_days=7)
    assert old["stale"] is True
    fresh = block({"x": 1}, "mother:t",
                  asof=date.today().isoformat(), max_age_days=7)
    assert fresh["stale"] is False


def test_daily_bars_mother_first_then_local(sd):
    # T53：基准日显式注入（不传就是墙钟，但断言依赖墙钟正是两次 flaky 的成因）
    out = mother.daily_bars(["600519", "000001"], days=5,
                            local_bars=_local_bar, mother=sd, today=D_LATEST)
    assert out["600519"]["source"] == "mother:kline_daily"
    assert out["600519"]["available"] and out["600519"]["asof"] == D_LATEST
    assert out["600519"]["bars"][0]["close"] == 1505
    assert out["600519"]["adj"] == "qfq"          # 有 close_qfq 用前复权
    # 基准日注入后 stale 与墙钟无关；fixture 又由真实今天倒推 ⇒ 两个前提都在
    assert out["600519"]["stale"] is False
    lag = (date.today() - date.fromisoformat(D_LATEST)).days
    assert 0 <= lag <= 3, f"fixture 的 D_LATEST={D_LATEST} 距今天 {lag} 天，超出新鲜窗口"
    assert out["000001"]["source"] == SRC_LOCAL_AKSHARE
    assert out["000001"]["available"]


def test_daily_bars_qfq_null_falls_back_hfq(sd):
    """close_qfq 为 NULL（老数据/回填中）逐行降级到 close_adj，不整体失败。"""
    out = mother.daily_bars(["300750"], days=5, local_bars=None, mother=sd)
    assert out["300750"]["source"] == "mother:kline_daily"
    assert out["300750"]["bars"][0]["close"] == 1200   # close_adj 后复权
    assert out["300750"]["adj"] == "hfq"


def test_scale_guard_rejects_polluted_mother(sd):
    """尺度闸：母库同日价与本地差 7.9 倍（事故场景）→ 拒用母库回退本地。"""
    def polluted_local(code, days):
        # 合成母库 600519 前一日 close=1495；本地给 189（差 ~8 倍）——必须同日
        return [{"timestamp": f"{D_PREV}T00:00:00", "open": 185, "high": 192,
                 "low": 180, "close": 189.0, "volume": 1000}]

    out = mother.daily_bars(["600519"], days=5, local_bars=polluted_local,
                            mother=sd)
    blk = out["600519"]
    assert blk["source"] == SRC_LOCAL_AKSHARE       # 母库被拒，回退本地
    assert blk["adj"] == "qfq"
    assert blk["scale_check"] and blk["scale_check"]["ok"] is False
    assert blk["bars"][0]["close"] == 189.0


def test_scale_guard_allows_small_diff(sd):
    """同日差 <10%（真实双源抖动）→ 母库正常放行。"""
    def close_local(code, days):
        return [{"timestamp": f"{D_PREV}T00:00:00", "open": 1490, "high": 1500,
                 "low": 1485, "close": 1493.0, "volume": 1000}]

    out = mother.daily_bars(["600519"], days=5, local_bars=close_local,
                            mother=sd)
    blk = out["600519"]
    assert blk["source"] == "mother:kline_daily"
    assert blk["scale_check"]["ok"] is True


def test_scale_guard_no_overlap_passes(sd):
    """两边无共有日期（本地滞后/母库独有）→ 无法对照则放行母库。"""
    def far_local(code, days):
        return [{"timestamp": "2026-09-01T00:00:00", "close": 100.0,
                 "open": 100, "high": 100, "low": 100, "volume": 1}]

    out = mother.daily_bars(["600519"], days=5, local_bars=far_local, mother=sd)
    blk = out["600519"]
    assert blk["source"] == "mother:kline_daily"
    assert blk["scale_check"] is None               # 无对照依据，不误杀


def test_daily_bars_unavailable_without_mother(tmp_path):
    out = mother.daily_bars(["600519"], local_bars=None, mother=tmp_path)
    assert out["600519"]["source"] == SRC_UNAVAILABLE
    assert not out["600519"]["available"] and out["600519"]["bars"] == []


def test_financial_summary_and_unavailable(sd):
    out = mother.financial_summary(["600519", "000001"], mother=sd)
    assert out["600519"]["source"] == "mother:financial_summary"
    assert out["600519"]["data"]["roe_5y_avg"] == 20.5
    assert out["000001"]["source"] == SRC_UNAVAILABLE


def test_dividend_benchmark_regime(sd):
    d = mother.dividend(["600519"], mother=sd)
    assert d["600519"]["source"] == "mother:dividend_annual"
    b = mother.benchmark(["000001"], mother=sd)
    assert b["000001"]["source"] == "mother:benchmark_daily"
    r = mother.regime(mother=sd)
    assert r["source"] == "mother:market_index" and r["data"][0]["index_code"] == "000001"


def test_news_mother_preferred_then_local(sd):
    out = mother.news(codes=["600519"], mother=sd)
    assert out["source"] == "mother:news_item"
    assert out["data"][0]["title"] == "测试标题"
    # 母库无该表时回退本地
    empty = sd.parent / "empty-sd"
    (empty / "src").mkdir(parents=True)
    (empty / "data" / "db").mkdir(parents=True)
    sqlite3.connect(empty / "data" / "db" / "stock_dashboard.db").close()
    mother.reset_cache()
    local = {"available": True, "items": [{"title": "本地快讯"}],
             "fetched_at": D_LATEST}
    out2 = mother.news(codes=["600519"], local_news=lambda: local, mother=empty)
    assert out2["source"] == SRC_LOCAL_AKSHARE


def test_screening_detail_and_local_pool(sd):
    out = mother.screening_detail(mother=sd)
    assert out["source"] == "mother:screening_result"
    assert out["data"]["rows"][0]["score_detail"] == '{"total":90}'
    out2 = mother.screening_detail(local_pool=["600519"],
                                  mother=sd.parent / "no-such-sd")
    assert out2["source"] == "local:config"


def test_thesis_and_outcomes(sd):
    t = mother.thesis(["600519"], mother=sd)
    assert t["600519"]["source"] == "mother:watchlist_thesis"
    o = mother.outcomes(["600519"], mother=sd)
    assert o["600519"]["source"] == "mother:analysis_outcome"


def test_instrument_map_and_trading_calendar(sd):
    im = mother.instrument_map(["600519", "000001"], mother=sd)
    assert im["600519"]["source"] == "mother:instrument"
    assert im["600519"]["data"]["instrument_id"] == "CN:600519"
    assert im["000001"]["source"] == SRC_UNAVAILABLE

    tc = mother.trading_calendar(mother=sd)
    assert tc["source"] == "mother:trading_calendar"
    assert any(r["is_open"] == 1 for r in tc["data"])


def test_calendar_sync_prefers_mother(sd, tmp_path, monkeypatch):
    """calendar sync 母库优先：母库 trading_calendar 可用即落缓存，不再调 akshare。"""
    from paper_trading.utils import trading_calendar as tc_mod

    # 日期必须落在工作日（today+10 可能是周六——本测试曾因此在周三翻车）
    fut_d = date.today() + timedelta(days=10)
    while fut_d.weekday() >= 5:
        fut_d += timedelta(days=1)
    fut = fut_d.isoformat()
    conn = sqlite3.connect(sd / "data" / "db" / "stock_dashboard.db")
    conn.execute("INSERT INTO trading_calendar VALUES ('CN',?,1,'{}','test')",
                 (fut,))
    conn.commit()
    conn.close()
    mother.reset_cache()
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(sd))
    cal = tmp_path / "cal.json"
    monkeypatch.setenv(tc_mod.CALENDAR_ENV, str(cal))
    out = tc_mod.sync_calendar()
    assert out["ok"] and out["source"] == "mother:trading_calendar"
    assert fut in tc_mod._read_calendar(cal)["dates"]
    assert tc_mod.is_trading_day(date.fromisoformat(fut))


def test_calendar_sync_history_only_falls_back(sd, tmp_path, monkeypatch):
    """母库日历只有历史（回填中，max < 今天）不算可用：回退 akshare。

    否则缓存 max_date 落在昨天，calendar_covers fail-closed 会让所有
    交易入口停摆（Pi 实测：母库 3949 行但无未来交易日）。
    """
    from paper_trading.utils import trading_calendar as tc_mod

    # 合成库日历行是 D_LATEST/D_NEXT（今天/明天）；把未来全删，模拟只回填历史
    conn = sqlite3.connect(sd / "data" / "db" / "stock_dashboard.db")
    conn.execute("DELETE FROM trading_calendar WHERE trade_date >= DATE('now')")
    conn.commit()
    conn.close()
    mother.reset_cache()
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(sd))
    cal = tmp_path / "cal.json"
    monkeypatch.setenv(tc_mod.CALENDAR_ENV, str(cal))
    import builtins

    real_import = builtins.__import__

    def _no_akshare(name, *a, **kw):
        if name == "akshare":
            raise ImportError("stubbed: no network")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_akshare)
    out = tc_mod.sync_calendar()
    assert out["ok"] is False          # 母库历史-only + akshare 不可用 → 失败但不写坏缓存
    assert not cal.exists() or "mother" not in cal.read_text(encoding="utf-8")


def test_compare_bars_local_vs_mother(sd):
    out = mother.compare_bars(
        ["600519"], days=10, mother=sd,
        local_bars=lambda c, d: [{"timestamp": f"{D_SEQ[i - 1]}T00:00:00",
                                  "open": 10, "high": 11, "low": 9,
                                  "close": 10 + i, "volume": 1}
                                 for i in range(1, 4)])
    r = out["600519"]
    assert r["local"]["adj"] == "qfq"
    assert r["mother"]["adj"] == "qfq"           # 母库有 close_qfq 时同口径
    assert r["cross_diff"] == {"golden": 0, "death": 0}
    assert "return_corr" in r


def test_coverage_summary(sd):
    cov = mother.coverage(["600519"], local_bars=lambda c, d: [], mother=sd)
    for k in ("daily_bars", "financial_summary", "instrument", "screening",
              "regime", "trading_calendar"):
        assert k in cov
    assert cov["instrument"]["600519"]["source"] == "mother:instrument"


def test_ttl_cache_hits_across_connections(sd):
    """R4.2 回归：缓存键必须跨连接稳定——旧实现用连接序号，永不命中。

    连续两次调用（各自新建连接）+ TTL 内母库被改：第二次必须命中内存
    缓存吐旧值。2026-10-27 审查发现：文档声称 60s/300s 缓存，实际
    因键含单调连接序号形同虚设。
    """
    db = sd / "data" / "db" / "stock_dashboard.db"
    # 填缓存（local_bars 恒空：尺度闸无对照依据则放行母库）
    out1 = mother.daily_bars(["600519"], days=5, local_bars=lambda c, d: [],
                             mother=sd, ttl=60.0)
    assert out1["600519"]["bars"][0]["close"] == 1505
    # 模拟母库更新（改值）
    c = sqlite3.connect(db)
    c.execute("UPDATE kline_daily SET close_qfq=9999, close_adj=9999, close=9999 "
              "WHERE code='600519' AND trade_date=?", (D_LATEST,))
    c.commit()
    c.close()
    # TTL 内第二次调用（独立连接）：命中缓存 → 仍是旧值 1505
    out2 = mother.daily_bars(["600519"], days=5, local_bars=lambda c, d: [],
                             mother=sd, ttl=60.0)
    assert out2["600519"]["bars"][0]["close"] == 1505, \
        "缓存未命中（键跨连接不稳定，回到旧 bug）"
    # TTL=0 强制回源：读到新值
    out3 = mother.daily_bars(["600519"], days=5, local_bars=lambda c, d: [],
                             mother=sd, ttl=0.0)
    assert out3["600519"]["bars"][0]["close"] == 9999
    # 还原（合成库供后续用例）
    c = sqlite3.connect(db)
    c.execute("UPDATE kline_daily SET close_qfq=1505, close_adj=1505, close=1505 "
              "WHERE code='600519' AND trade_date=?", (D_LATEST,))
    c.commit()
    c.close()
    mother.reset_cache()


def test_daily_bars_missing_table_falls_back(sd):
    """R4.1 表缺失态：有目录但库无 kline_daily 表 → 回退本地（不整链失败）。"""
    empty = sd.parent / "empty-sd"
    (empty / "src").mkdir(parents=True, exist_ok=True)
    (empty / "data" / "db").mkdir(parents=True, exist_ok=True)
    sqlite3.connect(empty / "data" / "db" / "stock_dashboard.db").close()
    mother.reset_cache()
    try:
        out = mother.daily_bars(["600519"], days=5, local_bars=_local_bar,
                                mother=empty)
        assert out["600519"]["source"] == SRC_LOCAL_AKSHARE
        assert out["600519"]["available"] and out["600519"]["bars"]
    finally:
        mother.reset_cache()


def test_locked_mother_falls_back_quickly(sd):
    """R4.4 母库被写锁占死：2s 硬超时内降级本地，不挂死（合成库，不碰真母库）。

    真母库上有母项目回填进程在写，不能人为加锁干扰；行为等价性在合成库
    验证（同一 _conn 路径：timeout=2 + busy_timeout=2000）。
    """
    import time

    db = sd / "data" / "db" / "stock_dashboard.db"
    locker = sqlite3.connect(db)
    mother.reset_cache()
    locker.execute("BEGIN EXCLUSIVE")       # 独占锁，读者全部阻塞
    t0 = time.monotonic()
    try:
        out = mother.daily_bars(["600519"], days=5, local_bars=_local_bar,
                                mother=sd)
    finally:
        locker.execute("ROLLBACK")
        locker.close()
        mother.reset_cache()
    elapsed = time.monotonic() - t0
    assert elapsed < 8.0, f"锁竞争下未在硬超时内返回：{elapsed:.1f}s"
    assert out["600519"]["source"] == SRC_LOCAL_AKSHARE
    assert out["600519"]["available"]      # 本地兜底照常供给


def test_local_fallback_sorts_and_asof_latest(sd):
    """H-6：本地回退必须自己排序，asof 取最新日（旧代码取 bars[0] 会失真）。"""
    def desc_local(code, days):
        # 故意降序返回：调用方顺序不可信
        return [{"timestamp": f"{D_SEQ[2]}T00:00:00", "open": 1495, "high": 1500,
                 "low": 1490, "close": 1495.0, "volume": 1},
                {"timestamp": f"{D_SEQ[0]}T00:00:00", "open": 1491, "high": 1496,
                 "low": 1486, "close": 1491.0, "volume": 1},
                {"timestamp": f"{D_SEQ[1]}T00:00:00", "open": 1493, "high": 1498,
                 "low": 1488, "close": 1493.0, "volume": 1}]

    out = mother.daily_bars(["600519"], days=5, local_bars=desc_local,
                            mother=sd.parent / "no-such-sd")
    blk = out["600519"]
    assert blk["source"] == SRC_LOCAL_AKSHARE
    assert [b["date"] for b in blk["bars"]] == \
        D_SEQ, "回退分支必须升序"
    assert blk["asof"] == D_SEQ[2], "asof 必须是最新日（非最老日）"


def test_abs_sanity_rejects_unit_error(sd):
    """H-7：close >10 万（单位级错误，如以分计价）无跨源对照也拒，回退本地。"""
    def far_local(code, days):
        # 日期与母库无重叠 → R1.3 跨源闸无对照依据，靠绝对量级闸兜底
        return [{"timestamp": "2026-09-01T00:00:00", "open": 22, "high": 23,
                 "low": 21, "close": 22.24, "volume": 100}]

    db = sd / "data" / "db" / "stock_dashboard.db"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE kline_daily SET close_qfq=250000, close_adj=250000, "
                 "close=250000 WHERE code='600519'")
    conn.commit()
    conn.close()
    mother.reset_cache()
    try:
        out = mother.daily_bars(["600519"], days=5, local_bars=far_local,
                                mother=sd)
        blk = out["600519"]
        assert blk["source"] == SRC_LOCAL_AKSHARE
        assert blk["bars"][0]["close"] == 22.24
    finally:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE kline_daily SET close_qfq=1505, close_adj=1505, "
                     "close=1505 WHERE code='600519'")
        conn.commit()
        conn.close()
        mother.reset_cache()


def test_real_mother_contract_if_present():
    """真母库契约校验：显式指定 ``PAPER_TRADING_MOTHER_DIR``（推荐）。

    单测默认隔离母库（见 ``conftest._isolated_mother``）；要在 Pi 上验证真库：

    ```bash
    PAPER_TRADING_MOTHER_DIR=/home/pi/stock-dashboard \\
      venv/bin/python -m pytest tests/test_mother_contract.py -q
    ```
    """
    import os

    explicit = os.environ.get("PAPER_TRADING_MOTHER_DIR")
    if not explicit:
        pytest.skip("未指定 PAPER_TRADING_MOTHER_DIR")
    sd = mother_dir(explicit)
    db = mother_db_path(sd) if sd is not None else None
    if db is None:
        pytest.skip(f"指定目录不是可用母库：{explicit}")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for table, cols in REQUIRED.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert have, f"母库缺表 {table}"
            missing = cols - have
            assert not missing, f"母库表 {table} 缺列 {missing}"
        # —— Issue #8 数据契约验收（母方 2026-10-27 交付）——
        # 1) 前复权口径：close_qfq 存在且近 30 天无 NULL（老数据允许回填中）
        if "close_qfq" in {r[1] for r in conn.execute(
                "PRAGMA table_info(kline_daily)")}:
            n_null = conn.execute(
                "SELECT COUNT(*) FROM kline_daily WHERE close_qfq IS NULL "
                "AND trade_date >= DATE('now', '-30 day')").fetchone()[0]
            assert n_null == 0, f"近 30 天 close_qfq 有 {n_null} 行 NULL"
        # 2) 基准代码：子项目使用的三个指数代码必须可查（Issue #5）
        codes = {r[0] for r in conn.execute(
            "SELECT DISTINCT code FROM benchmark_daily")}
        assert {"000001", "399001", "399006"} & codes, \
            f"benchmark_daily 缺常用指数代码，现有 {sorted(codes)[:8]}"
        # 3) 交易日历：有数据且覆盖今天（Issue #6；空表=回填未完成）
        n_cal = conn.execute(
            "SELECT COUNT(*) FROM trading_calendar WHERE is_open = 1"
        ).fetchone()[0]
        assert n_cal > 0, "trading_calendar 无 is_open=1 行（回填未完成）"
        # 4) 量级 sanity（R1.6 固化）：母库与本地同日重叠抽样比值 [0.9,1.1]
        #    ——尺度污染（复权口径/取错列）是 1.2×~8× 量级，比值检查必拦
        if "close_qfq" in {r[1] for r in conn.execute(
                "PRAGMA table_info(kline_daily)")}:
            local_db = Path("data.db")
            if local_db.exists():
                loc = sqlite3.connect(f"file:{local_db}?mode=ro", uri=True)
                try:
                    rows = conn.execute(
                        """SELECT code, trade_date, COALESCE(close_qfq, close)
                           FROM kline_daily WHERE trade_date >= DATE('now','-7 day')
                           LIMIT 200""").fetchall()
                    bad, cmp_n = [], 0
                    for code, mdate, mclose in rows:
                        same = loc.execute(
                            """SELECT close FROM daily_bars WHERE symbol=?
                               AND timestamp>=? AND timestamp<?""",
                            (code, mdate, mdate + "T23:59:59")).fetchone()
                        if not same or not same[0] or not mclose:
                            continue
                        cmp_n += 1
                        ratio = mclose / same[0]
                        if ratio > 1.1 or ratio < 0.9:
                            bad.append((code, mdate, round(ratio, 3)))
                    assert not bad, f"母库/本地同日量级异常：{bad[:5]}"
                    # 有重叠才断言（回填期可能 0 可比，不误杀）
                    if cmp_n == 0:
                        pytest.skip("本地库与母库近 7 日无重叠，无法量级对照")
                finally:
                    loc.close()
    finally:
        conn.close()
