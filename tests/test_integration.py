"""integration 连接处单测：fake 母库可读 / 缺失回退 / 永不写母库。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.integration import (  # noqa: E402
    is_merged,
    mother_db_path,
    read_market_regime,
    read_stock_cards,
    resolve_pool,
)


def _mother(tmp: Path) -> Path:
    mdir = tmp / "stock-dashboard"
    dbdir = mdir / "data" / "db"
    dbdir.mkdir(parents=True)
    (mdir / "src").mkdir()
    db = dbdir / "stock_dashboard.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE screening_result (run_id TEXT, run_date TEXT, code TEXT,"
              " name TEXT, score REAL, pe REAL, pb REAL, roe REAL, reason TEXT,"
              " status TEXT)")
    c.execute("INSERT INTO screening_result VALUES "
              "('20260928_000000','2026-09-28','600519','贵州茅台',95,20,5,25,'好','active'),"
              "('20260928_000000','2026-09-28','000001','上证A',10,99,99,1,'差','eliminated')")
    c.execute("CREATE TABLE ai_watchlist (code TEXT PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO ai_watchlist VALUES ('600036','招商银行')")
    c.execute("CREATE TABLE watchlist (code TEXT PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO watchlist VALUES ('000858','五粮液')")
    c.execute("CREATE TABLE stock_snapshot (code TEXT PRIMARY KEY, name TEXT, sector TEXT,"
              " pe REAL, pb REAL, market_cap REAL, roe REAL, dividend_yield REAL,"
              " current_price REAL, high_52w REAL, low_52w REAL, is_st INTEGER)")
    c.execute("INSERT INTO stock_snapshot VALUES "
              "('600519','贵州茅台','白酒',20,5,20000,25,1.5,1400,1500,1200,0)")
    c.execute("CREATE TABLE stock_analysis_history (stock_code TEXT, analysis_date TEXT,"
              " score REAL, ai_analysis TEXT, ai_trade_strategy TEXT, model TEXT)")
    c.execute("INSERT INTO stock_analysis_history VALUES "
              "('600519','2026-09-27',90,'好公司','持有','m')")
    c.execute("CREATE TABLE watchlist_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,"
              " code TEXT, note TEXT, note_type TEXT, model TEXT)")
    c.execute("INSERT INTO watchlist_notes (code, note, note_type, model) VALUES "
              "('600519','分红稳定','weekly','m')")
    c.execute("CREATE TABLE watchlist_thesis (code TEXT, core_thesis TEXT,"
              " sell_conditions TEXT)")
    c.execute("INSERT INTO watchlist_thesis VALUES "
              "('600519','护城河深','[{\"condition\": \"跌破MA20卖出\"}]')")
    c.execute("CREATE TABLE market_index (index_code TEXT, index_name TEXT,"
              " current_value REAL, change_percent REAL, pe REAL, pb REAL, date TEXT)")
    c.execute("INSERT INTO market_index VALUES "
              "('000001','上证综指',3000,0.5,13,1.5,'2026-09-27')")
    c.commit()
    c.close()
    return mdir


def test_pool_config_default_no_mother(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "nothing"))
    syms, note, cands = resolve_pool(None, ["600519"], "config")
    assert syms == ["600519"] and note == "config" and cands == []
    assert not is_merged(str(tmp_path / "nothing"))


def test_pool_watchlist_and_screening(tmp_path, monkeypatch):
    mdir = _mother(tmp_path)
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(mdir))
    assert is_merged()
    syms, note, _ = resolve_pool(None, ["600519"], "watchlist")
    assert syms == ["600036", "000858"] and note == "watchlist"
    syms, note, cands = resolve_pool(None, ["600519"], "screening", pool_limit=20)
    assert syms == ["600519"] and "screening" in note
    assert cands[0]["score"] == 95 and "eliminated" not in str(cands)
    syms, note, _ = resolve_pool(None, ["600519"], "all")
    assert syms == ["600519", "600036", "000858"] and note.startswith("all")
    syms, note, _ = resolve_pool(["1"], ["600519"], "screening")
    assert syms == ["000001"] and note == "cli"  # CLI 显式优先（短码补零）


def test_search_symbols_from_full_market_table(tmp_path, monkeypatch):
    """AI 提名解析：母库全市场代码表（5527 只量级）按代码/名称/模糊匹配，只读。"""
    from paper_trading.integration import is_a_share, search_symbols, universe_size

    mdir = _mother(tmp_path)
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(mdir))
    db = mother_db_path()
    before = db.stat().st_mtime_ns
    assert universe_size() == 1        # fake 母库只有 1 只
    # 代码精确
    got = search_symbols(["600519"])
    assert got and got[0]["symbol"] == "600519" and got[0]["name"] == "贵州茅台"
    assert got[0]["matched"] == "code"
    # 名称精确 / 模糊
    assert search_symbols(["贵州茅台"])[0]["matched"] == "name"
    assert search_symbols(["茅台"])[0]["symbol"] == "600519"
    assert search_symbols(["茅台"])[0]["matched"] == "name~"
    # 噪声词与非法代码一律拒绝
    assert search_symbols(["沪深300指数", "上证ETF"]) == []
    assert search_symbols(["999999"]) == []          # 表里没有
    # 去重
    assert len(search_symbols(["600519", "贵州茅台"])) == 1
    assert is_a_share("600519") and is_a_share("000858") and is_a_share("300750")
    assert not is_a_share("510300") and not is_a_share("12345") and not is_a_share("")
    assert db.stat().st_mtime_ns == before            # 只读，未写母库
    # 母库缺席：退化本地表，仍不崩
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "nothing"))
    assert universe_size() == 0
    assert search_symbols(["600519"], local_names={"600519": "贵州茅台"})[0]["matched"] \
        == "local"
    assert search_symbols(["不存在的票"], local_names={"600519": "贵州茅台"}) == []


def test_pool_auto_prefers_screening(tmp_path, monkeypatch):
    """auto：全市场筛选优先，缺失时降级观察池，再降级手填池（手填只兜底）。"""
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "nothing"))
    syms, note, c = resolve_pool(None, ["600519"], "auto")
    assert syms == ["600519"] and "fallback" in note and c == []
    mdir = _mother(tmp_path)
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(mdir))
    syms, note, c = resolve_pool(None, ["111111"], "auto")
    assert syms == ["600519"] and note.startswith("screening") and c
    assert "111111" not in syms          # 手填池不叠加进 auto
    # all 不再含手填池
    syms, note, _ = resolve_pool(None, ["111111"], "all")
    assert syms == ["600519", "600036", "000858"] and note.startswith("all")


def test_analysis_readonly_and_content(tmp_path, monkeypatch):
    mdir = _mother(tmp_path)
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(mdir))
    db = mother_db_path()
    before = db.stat().st_mtime_ns
    cards = read_stock_cards(["600519", "999999"])
    assert cards["600519"]["fundamentals"]["sector"] == "白酒"
    assert cards["600519"]["ai_analysis"]["score"] == 90
    assert cards["600519"]["notes"][0]["note"] == "分红稳定"
    assert "MA20" in cards["600519"]["thesis"]["sell_conditions"]
    assert "999999" not in cards
    regime = read_market_regime()
    assert regime and regime[0]["index"] == "上证综指"
    assert db.stat().st_mtime_ns == before  # 只读，未写母库


def test_analysis_missing_mother_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "nothing"))
    assert read_stock_cards(["600519"]) == {}
    assert read_market_regime() == []
