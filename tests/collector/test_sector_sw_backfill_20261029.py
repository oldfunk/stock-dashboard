"""批9-b sector 行业扩图回归测试（2026-10-29）。

全程 mock，不打网络、不碰生产库。守的承诺：
1. 申万接缝（行业名单 / 成分）的取列、补零与脏值过滤；
2. 逐行业失败只记不抛（部分映射仍可用，且**不覆盖缓存**当"最新全量"）；
3. 划分性：同一 code 落进两个行业要计数并保留先归入的，不静默改写；
4. 缓存优先 / 超时护栏 / 回退链；
5. `merge_sector_maps` 主源优先、回退只补缺口、两边都没有的不进 map；
6. 脚本落库只碰 map 里的 code：未收录新股保持 NULL（不臆造），dry-run 零写入，
   两个源都空时不写并带 error。
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "sec.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


@pytest.fixture
def cache(tmp_path, monkeypatch):
    import src.collector.akshare_fetcher as fetcher
    monkeypatch.setattr(fetcher, "SECTOR_SW_CACHE_FILE",
                        str(tmp_path / "sector_map_sw.json"))
    return fetcher


def _snap(code, sector=None):
    return {"code": code, "name": "X", "market": "A", "sector": sector,
            "pe": 10.0, "pb": 1.0, "ps": 1.0, "market_cap": 1.0,
            "circulating_cap": 1.0, "roe": 10.0, "revenue": 1.0,
            "revenue_growth": 5.0, "profit": 1.0, "profit_growth": 5.0,
            "debt_ratio": 30.0, "current_price": 1.0, "high_52w": 1.0,
            "low_52w": 1.0, "is_st": False, "list_date": "2010-01-01",
            "snapshot_date": "2026-10-29"}


def _sectors():
    with db_mod.db_conn() as conn:
        return {r["code"]: r["sector"] for r in conn.execute(
            "SELECT code, sector FROM stock_snapshot")}


# ── 1. 申万接缝 ────────────────────────────────────────────────────────────

def test_sw_first_industries_strips_index_suffix(cache, monkeypatch):
    df = pd.DataFrame([
        {"行业代码": "801010.SI", "行业名称": "农林牧渔", "成份个数": 104},
        {"行业代码": "801030.SI", "行业名称": "基础化工", "成份个数": 413},
    ])
    monkeypatch.setattr(cache, "ak",
                        SimpleNamespace(sw_index_first_info=lambda: df))
    assert cache._sw_first_industries() == [("801010", "农林牧渔"),
                                            ("801030", "基础化工")]


def test_sw_industry_members_zfills_and_drops_dirty(cache, monkeypatch):
    """成分列：数字补零到 6 位；None/空/非数字一律丢（不拿 '000000' 当代码）。"""
    df = pd.DataFrame({"证券代码": ["000505", 600519, None, "", "SH600000", "1"]})
    monkeypatch.setattr(cache, "ak",
                        SimpleNamespace(index_component_sw=lambda symbol: df))
    assert cache._sw_industry_members("801010") == ["000505", "600519", "000001"]


def test_sw_industry_members_without_code_column(cache, monkeypatch):
    """上游换列名时返回空列表，而不是抓第一列当代码乱写。"""
    df = pd.DataFrame({"证券名称": ["京粮控股"]})
    monkeypatch.setattr(cache, "ak",
                        SimpleNamespace(index_component_sw=lambda symbol: df))
    assert cache._sw_industry_members("801010") == []


# ── 2/3. 逐行业取成分 ──────────────────────────────────────────────────────

def _patch_sw(monkeypatch, fetcher, industries, members, fail=()):
    def fake_first():
        return list(industries)

    def fake_members(code):
        if code in fail:
            raise RuntimeError("镜像挂了")
        return list(members.get(code, []))

    monkeypatch.setattr(fetcher, "_sw_first_industries", fake_first)
    monkeypatch.setattr(fetcher, "_sw_industry_members", fake_members)


def test_fetch_sw_map_partial_failure_keeps_the_rest(cache, monkeypatch):
    industries = [("801010", "农林牧渔"), ("801030", "基础化工")]
    members = {"801010": ["000505"], "801030": ["600519"]}
    _patch_sw(monkeypatch, cache, industries, members, fail={"801030"})
    mapping, stats = cache._fetch_sw_sector_map_impl(sleep=0)
    assert mapping == {"000505": "农林牧渔"}
    assert stats["industries"] == 2
    assert [e[0] for e in stats["errors"]] == ["基础化工"]
    assert "RuntimeError" in stats["errors"][0][1]


def test_fetch_sw_map_duplicate_membership_counts_and_keeps_first(cache, monkeypatch):
    """划分性破了要看得见：计数 + 保留先归入的行业（不静默按抓取顺序改写）。"""
    industries = [("801010", "农林牧渔"), ("801030", "基础化工")]
    members = {"801010": ["000505"], "801030": ["000505", "600519"]}
    _patch_sw(monkeypatch, cache, industries, members)
    mapping, stats = cache._fetch_sw_sector_map_impl(sleep=0)
    assert mapping["000505"] == "农林牧渔"
    assert stats["duplicates"] == 1


def test_fetch_sw_map_records_empty_industry(cache, monkeypatch):
    industries = [("801010", "农林牧渔"), ("801030", "基础化工")]
    members = {"801010": [], "801030": ["600519"]}
    _patch_sw(monkeypatch, cache, industries, members)
    mapping, stats = cache._fetch_sw_sector_map_impl(sleep=0)
    assert mapping == {"600519": "基础化工"}
    assert stats["errors"] == [["农林牧渔", "空成分"]]


# ── 4. 缓存与兜底链 ────────────────────────────────────────────────────────

def test_cache_age_gate(cache):
    today = cache.now_cn().date()
    assert cache._cache_age_ok(today.strftime("%Y-%m-%d"), 30) is True
    assert cache._cache_age_ok((today - timedelta(days=31)).strftime("%Y-%m-%d"), 30) \
        is False
    assert cache._cache_age_ok((today + timedelta(days=1)).strftime("%Y-%m-%d"), 30) \
        is False                      # 未来日期不可信（时钟/写坏）
    assert cache._cache_age_ok("not-a-date", 30) is False
    assert cache._cache_age_ok(None, 30) is False


def test_fresh_cache_skips_network(cache, monkeypatch):
    cache._save_map_cache(cache.SECTOR_SW_CACHE_FILE, {"600519": "食品饮料"})

    def boom(sleep=0):
        raise AssertionError("缓存够新时不该打网络")

    monkeypatch.setattr(cache, "_fetch_sw_sector_map_impl", boom)
    result = cache.fetch_sw_sector_map()
    assert result["source"] == "cache" and result["map"] == {"600519": "食品饮料"}


def test_force_ignores_cache_and_small_result_does_not_overwrite(cache, monkeypatch):
    cache._save_map_cache(cache.SECTOR_SW_CACHE_FILE, {"600519": "食品饮料"})
    _patch_sw(monkeypatch, cache, [("801010", "农林牧渔")], {"801010": ["000505"]})
    result = cache.fetch_sw_sector_map(force=True, sleep=0)   # 5 只 << MIN
    assert result["source"] == "live" and result["map"] == {"000505": "农林牧渔"}
    with open(cache.SECTOR_SW_CACHE_FILE, encoding="utf-8") as f:
        assert json.load(f)["map"] == {"600519": "食品饮料"}   # 半截映射没覆盖缓存


def test_live_result_above_min_writes_cache(cache, monkeypatch):
    monkeypatch.setattr(cache, "SW_SECTOR_MAP_MIN", 1)
    _patch_sw(monkeypatch, cache, [("801010", "农林牧渔")], {"801010": ["000505"]})
    result = cache.fetch_sw_sector_map(force=True, sleep=0)
    assert result["source"] == "live"
    with open(cache.SECTOR_SW_CACHE_FILE, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["map"] == {"000505": "农林牧渔"} and payload["fetched"]


def test_live_failure_falls_back_to_stale_cache(cache, monkeypatch):
    """实时全挂 -> 用旧缓存救场，但 error 要说清（不能用兜底冒充成功）。"""
    with open(cache.SECTOR_SW_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched": "2020-01-01", "map": {"600519": "食品饮料"}}, f)

    def boom():
        raise RuntimeError("镜像不可达")

    monkeypatch.setattr(cache, "_sw_first_industries", boom)
    result = cache.fetch_sw_sector_map(sleep=0)
    assert result["map"] == {"600519": "食品饮料"}
    assert result["source"] == "cache" and "RuntimeError" in result["error"]


def test_timeout_guard_returns_error_not_hang(cache, monkeypatch):
    import threading

    barrier = threading.Event()

    def slow(sleep=0):
        barrier.wait(0.2)          # 比 timeout 长一点点
        return {}, {}

    monkeypatch.setattr(cache, "_fetch_sw_sector_map_impl", slow)
    result = cache.fetch_sw_sector_map(timeout=0, force=True)
    assert result["map"] == {} and result["source"] == "empty"
    assert "超时" in result["error"]
    barrier.set()


def test_guard_reports_impl_exception(cache):
    def boom():
        raise ValueError("上游坏了")

    result, error = cache._guarded(boom, timeout=5)
    assert result is None and isinstance(error, ValueError)


# ── 5. 合并口径 ────────────────────────────────────────────────────────────

def test_merge_primary_wins_and_fallback_only_fills_gaps(cache):
    merged = cache.merge_sector_maps(
        {"600519": "食品饮料", "000001": "银行"},
        {"600519": "白酒（新浪旧口径）", "300750": "电力设备", "": "脏", "000002": ""})
    assert merged == {"600519": "食品饮料", "000001": "银行", "300750": "电力设备"}


def test_merge_empty_inputs_are_empty_not_invented(cache):
    assert cache.merge_sector_maps({}, {}) == {}
    assert cache.merge_sector_maps(None, {"600519": "食品饮料"}) == {"600519": "食品饮料"}


# ── 6. 脚本落库 ────────────────────────────────────────────────────────────

def _patch_sources(monkeypatch, script, sw, sina):
    monkeypatch.setattr(
        script, "fetch_sw_sector_map",
        lambda force=False: {"map": sw, "source": "live" if sw else "empty",
                             "stats": {"industries": 1}, "error": None if sw else "空"})
    monkeypatch.setattr(script, "fetch_sector_map", lambda: dict(sina))


def test_script_fills_missing_and_keeps_unmapped_null(db, monkeypatch):
    import scripts.backfill_sectors_sw as script
    db_mod.StockSnapshotDAO().save_batch([
        _snap("600519", "白酒"),        # 新浪旧口径 -> 应被改写成申万一级
        _snap("000001", None),          # 缺口 -> 申万补上
        _snap("301999", None),          # 两个源都没有 -> 继续 NULL（不臆造）
        _snap("002731", None),          # 只有新浪有 -> 用回退，计进 sina_only
    ])
    _patch_sources(monkeypatch, script,
                   {"600519": "食品饮料", "000001": "银行"},
                   {"002731": "次新股", "600519": "白酒"})
    result = script.run()
    assert _sectors() == {"600519": "食品饮料", "000001": "银行",
                          "301999": None, "002731": "次新股"}
    assert result["merged"] == 3 and result["sina_only_codes"] == 1
    assert result["missing_before"] == 3 and result["fill_actual"] == 2
    assert result["predicted_fill"] == result["fill_actual"]
    assert result["missing_after"] == 1
    assert result["updated"] == 3       # map 里 3 个 code 都被 UPDATE 命中


def test_script_dry_run_writes_nothing(db, monkeypatch):
    import scripts.backfill_sectors_sw as script
    db_mod.StockSnapshotDAO().save_batch([_snap("000001", None)])
    _patch_sources(monkeypatch, script, {"000001": "银行"}, {})
    result = script.run(dry_run=True)
    assert _sectors() == {"000001": None}
    assert result["updated"] == 0 and result["predicted_fill"] == 1
    assert result["predicted_missing_after"] == 0


def test_script_without_sina_leaves_gap(db, monkeypatch):
    import scripts.backfill_sectors_sw as script
    db_mod.StockSnapshotDAO().save_batch([_snap("002731", None)])
    _patch_sources(monkeypatch, script, {"600519": "食品饮料"}, {"002731": "次新股"})
    result = script.run(use_sina=False)
    assert result["sina_codes"] == 0 and result["merged"] == 1
    assert _sectors() == {"002731": None}


def test_script_refuses_when_both_sources_empty(db, monkeypatch, capsys):
    import scripts.backfill_sectors_sw as script
    db_mod.StockSnapshotDAO().save_batch([_snap("000001", "银行")])
    _patch_sources(monkeypatch, script, {}, {})
    assert script.main(["--no-sina"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "不写库" in payload["error"]
    assert _sectors() == {"000001": "银行"}          # 现值未被清空


def test_script_zero_writes_when_map_hits_no_snapshot_rows(db, monkeypatch):
    """映射里的票都不在 snapshot（新上市未进池）——如实报 updated=0，不吹成扩图成功。"""
    import scripts.backfill_sectors_sw as script
    db_mod.StockSnapshotDAO().save_batch([_snap("000001", None)])
    _patch_sources(monkeypatch, script, {"301688": "电子"}, {})
    result = script.run()
    assert result["updated"] == 0 and result["fill_actual"] == 0
    assert _sectors() == {"000001": None}
