"""批10-a 季报披露日回填回归测试（2026-10-29，全程 mock，不打网络）。

守的承诺（对应脚本 docstring 的五节）：
1. 只填 ``report_type='Q'`` 且 notice_date 为空的行——不碰年报、不覆盖已有值；
2. 库里没有的报告期命中 0 行，绝不因回填造出半截行；
3. 上游 ``REPORT_TYPE`` 没有 '季报' 这个值——筛的是 一季报/中报/三季报 三类；
4. 清洗闸门：非 YYYY-MM-DD、1900 类占位、早于报告期的披露日一律不写并计数；
5. 进度表 ``notice_date_quarterly``：done/no_data 记 covered=最新季报报告期（新季报
   到达自动重新入选），partial **不记** covered（下次自动重试）；
6. 连续失败中止、时间预算、--dry-run 零写入、--codes 必跑、CLI 出口码。
"""

import json
import re

import pytest

from src.models import database as db_mod

Q1_2026 = "2026-03-31"
Q3_2025 = "2025-09-30"
ANNUAL_2025 = "2025-12-31"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "nd.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _save(code, dates_notice):
    """dates_notice: [(report_date, notice_date 或 None)]，走既有 batch_save 口径。"""
    db_mod.FinancialHistoryDAO().batch_save([
        {"stock_code": code, "report_date": d, "notice_date": nd, "roe": 10.0}
        for d, nd in dates_notice])


def _notices(code):
    with db_mod.db_conn() as conn:
        return {r["report_date"]: r["notice_date"] for r in conn.execute(
            "SELECT report_date, notice_date FROM financial_history"
            " WHERE stock_code = ?", (code,))}


def _patch_fetch(monkeypatch, script, by_code, default_error=None):
    """by_code: {code: {"notices": {...}, "error": str|None}}；未列出的走 default_error。"""

    def fake_fetch(code, ps=100, timeout=15):
        payload = by_code.get(code, {})
        notices = payload.get("notices", {})
        return {"notices": notices, "rejected": payload.get("rejected", 0),
                "rows": payload.get("rows", 0),
                "error": payload.get("error") or default_error}

    monkeypatch.setattr(script, "fetch_mainfin_quarterly_notices", fake_fetch)


# ── 1/2. DAO 写入口径 ──────────────────────────────────────────────────────

def test_targets_list_only_codes_with_quarterly_gap(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None), (ANNUAL_2025, "2026-04-17")])   # 季报有空缺
    _save("000001", [(Q1_2026, "2026-04-25")])                        # 已填全
    _save("600036", [(ANNUAL_2025, None)])                            # 只有年报行
    assert dao.targets_missing_notice_quarterly() == [("600519", Q1_2026)]


def test_fill_touches_only_quarterly_null_rows(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None),            # 目标：季报 + 空
                     (Q3_2025, "2025-10-30"),    # 季报但已有值 -> 不覆盖
                     (ANNUAL_2025, None)])       # 年报空 -> 本件不碰（另有口径）
    filled = dao.fill_quarterly_notice_dates("600519", {
        Q1_2026: "2026-04-25", Q3_2025: "2025-11-01", ANNUAL_2025: "2026-04-17"})
    assert filled == 1
    assert _notices("600519") == {Q1_2026: "2026-04-25",
                                 Q3_2025: "2025-10-30",     # 原值保留
                                 ANNUAL_2025: None}


def test_unknown_report_date_creates_no_rows(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None)])
    filled = dao.fill_quarterly_notice_dates(
        "600519", {"2019-03-31": "2019-04-25", Q1_2026: "2026-04-25"})
    assert filled == 1
    assert set(_notices("600519")) == {Q1_2026}      # 不新增行


def test_dry_run_uses_same_predicate_and_writes_nothing(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None), (Q3_2025, "2025-10-30")])
    predicted = dao.fill_quarterly_notice_dates(
        "600519", {Q1_2026: "2026-04-25", Q3_2025: "2025-11-01"}, dry_run=True)
    assert predicted == 1                       # 只数"能填的"，已有值不计
    assert _notices("600519")[Q1_2026] is None  # 一行都没写


def test_rerun_is_idempotent(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None)])
    assert dao.fill_quarterly_notice_dates("600519", {Q1_2026: "2026-04-25"}) == 1
    assert dao.fill_quarterly_notice_dates("600519", {Q1_2026: "2099-01-01"}) == 0
    assert _notices("600519")[Q1_2026] == "2026-04-25"   # 第二次没有改写


# ── 3/4. 上游解析与清洗闸门 ────────────────────────────────────────────────

def test_clean_notice_date_rules():
    from src.collector.mainfin_fetcher import clean_notice_date
    assert clean_notice_date(Q1_2026, "2026-04-25 00:00:00") == "2026-04-25"
    assert clean_notice_date(Q1_2026, None) is None
    assert clean_notice_date(Q1_2026, "") is None
    assert clean_notice_date(Q1_2026, "2026-04-25") == "2026-04-25"
    assert clean_notice_date(Q1_2026, "1900-01-01") is None      # 占位值由"不早于报告期"挡住
    assert clean_notice_date(Q1_2026, "2026/04/25") is None      # 格式不对
    assert clean_notice_date(Q1_2026, "2026-03-01") is None      # 早于报告期
    assert clean_notice_date("", "2026-03-01") == "2026-03-01"   # 无报告期时只比格式


def test_fetch_quarterly_notices_filters_report_types(monkeypatch):
    from src.collector import mainfin_fetcher as mf
    captured = {}

    def fake_get_json(url, timeout=15, headers=None):
        captured["url"] = url
        return {"result": {"data": [
            {"REPORT_TYPE": "一季报", "REPORT_DATE": "2026-03-31 00:00:00",
             "NOTICE_DATE": "2026-04-25 00:00:00"},
            {"REPORT_TYPE": "中报", "REPORT_DATE": "2026-06-30 00:00:00",
             "NOTICE_DATE": "2026-08-15 00:00:00"},
            {"REPORT_TYPE": "三季报", "REPORT_DATE": "2025-09-30 00:00:00",
             "NOTICE_DATE": "2025-10-30 00:00:00"},
            {"REPORT_TYPE": "年报", "REPORT_DATE": "2025-12-31 00:00:00",
             "NOTICE_DATE": "2026-04-17 00:00:00"},
            {"REPORT_TYPE": "一季报", "REPORT_DATE": "2025-03-31 00:00:00",
             "NOTICE_DATE": "1900-01-01 00:00:00"},        # 占位 -> rejected
        ]}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    out = mf.fetch_mainfin_quarterly_notices("600519")
    assert out["notices"] == {Q1_2026: "2026-04-25", "2026-06-30": "2026-08-15",
                             Q3_2025: "2025-10-30"}
    assert out["rejected"] == 1 and out["rows"] == 5 and out["error"] is None
    # 关键取证点：不能加 (REPORT_TYPE="季报") ——东财没有这个取值，加了稳定 0 行
    assert "REPORT_TYPE" not in captured["url"]
    assert "ps=100" in captured["url"] and "600519.SH" in captured["url"]


def test_fetch_quarterly_notices_network_error_is_not_empty_result(monkeypatch):
    """网络失败必须带 error：把它当"这只票没有季报披露日"会让续传永久跳过它。"""
    from src.collector import mainfin_fetcher as mf

    def boom(url, timeout=15, headers=None):
        raise TimeoutError("上游无响应")

    monkeypatch.setattr(mf, "_get_json", boom)
    out = mf.fetch_mainfin_quarterly_notices("600519")
    assert out["notices"] == {} and "TimeoutError" in out["error"]


def test_sz_code_uses_sz_secucode(monkeypatch):
    from src.collector import mainfin_fetcher as mf
    seen = {}

    def fake_get_json(url, timeout=15, headers=None):
        seen["url"] = url
        return {"result": {"data": []}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    mf.fetch_mainfin_quarterly_notices("000001")
    assert "000001.SZ" in seen["url"]


def _page(ps: int, first_year: int):
    """造一整页：年报 + 三类季报混排（东财就是混在一起的），报告期倒序。"""
    rows = []
    for i in range(ps):
        year = first_year - i // 4
        kind = ("年报", "一季报", "中报", "三季报")[i % 4]
        month = ("12", "03", "06", "09")[i % 4]
        rows.append({"REPORT_TYPE": kind,
                     "REPORT_DATE": f"{year}-{month}-28 00:00:00",
                     "NOTICE_DATE": f"{year}-{month}-28 00:00:00"})
    return rows


def test_fetch_pages_until_short_page(monkeypatch):
    """第一页给满就必须翻；短页即停，不多打（000001 实测差 11 个季度）。"""
    from src.collector import mainfin_fetcher as mf
    pages = []

    def fake_get_json(url, timeout=15, headers=None):
        page = int(re.search(r"[&?]p=(\d+)", url).group(1))
        pages.append(page)
        data = _page(100, 2026) if page == 1 else _page(3, 2001)
        return {"result": {"data": data}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    out = mf.fetch_mainfin_quarterly_notices("000001")
    assert pages == [1, 2]
    assert out["rows"] == 103
    assert any(d.startswith("2001") for d in out["notices"])   # 第二页的老季度也收到


def test_fetch_respects_max_pages(monkeypatch):
    from src.collector import mainfin_fetcher as mf
    pages = []

    def fake_get_json(url, timeout=15, headers=None):
        page = int(re.search(r"[&?]p=(\d+)", url).group(1))
        pages.append(page)
        return {"result": {"data": _page(100, 2026 - page)}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    mf.fetch_mainfin_quarterly_notices("000001")
    assert pages == [1, 2, 3]                      # 上限内不无限翻


def test_later_page_failure_keeps_earlier_results_but_reports_error(monkeypatch):
    """翻第二页时挂掉：已有结果照样能用，但必须带 error（调用方据此判 partial）。"""
    from src.collector import mainfin_fetcher as mf

    def fake_get_json(url, timeout=15, headers=None):
        page = int(re.search(r"[&?]p=(\d+)", url).group(1))
        if page == 1:
            return {"result": {"data": _page(100, 2026)}}
        raise TimeoutError("第 2 页无响应")

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    out = mf.fetch_mainfin_quarterly_notices("000001")
    assert out["notices"] and out["rows"] == 100
    assert "TimeoutError" in out["error"]


# ── 5. 进度表语义 ──────────────────────────────────────────────────────────

def test_run_records_progress_and_resumes(db, monkeypatch):
    import scripts.backfill_notice_date_quarterly as script
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None)])
    _save("000001", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, script, {
        "600519": {"notices": {Q1_2026: "2026-04-25"}},
        "000001": {"error": "ConnectionError: 限流"},
    })
    result = script.run(sleep=0)
    assert result["filled"] == 1
    assert result["status_counts"] == {"done": 1, "partial": 1, "no_data": 0}
    assert result["errors"] == [["000001", "ConnectionError: 限流"]]
    assert _notices("600519")[Q1_2026] == "2026-04-25"
    assert dao.backfill_progress_summary(script.TASK)["done"] == 1
    # 下次目标集：done 被排除，partial 仍在（covered 没写）
    assert dao.targets_missing_notice_quarterly(task=script.TASK) == [("000001", Q1_2026)]

    _patch_fetch(monkeypatch, script, {"000001": {"notices": {Q1_2026: "2026-04-20"}}})
    again = script.run(sleep=0)
    assert again["targets"] == 1 and again["filled"] == 1
    assert again["remaining"] == 0


def test_new_quarter_reenters_target_set(db, monkeypatch):
    """covered=最新季报报告期：新一季落地就自动重新入选，不被进度表锁死。"""
    import scripts.backfill_notice_date_quarterly as script
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, script, {"600519": {"notices": {Q1_2026: "2026-04-25"}}})
    script.run(sleep=0)
    assert dao.targets_missing_notice_quarterly(task=script.TASK) == []
    _save("600519", [("2026-06-30", None)])        # 新一季到来
    assert dao.targets_missing_notice_quarterly(
        task=script.TASK) == [("600519", "2026-06-30")]


def test_no_data_marks_covered_but_partial_does_not(db, monkeypatch):
    import scripts.backfill_notice_date_quarterly as script
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None)])
    _save("000001", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, script, {
        "600519": {"notices": {}, "rows": 30},              # 上游没给季报披露日
        "000001": {"notices": {}, "error": "ValueError: 上游空返回"},
    })
    script.run(sleep=0)
    with db_mod.db_conn() as conn:
        covered = dict(conn.execute(
            "SELECT code, covered_report_date FROM backfill_progress"
            " WHERE task = ?", (script.TASK,)).fetchall())
    assert covered["600519"] == Q1_2026          # no_data：记 covered，不再天天重试
    assert covered["000001"] is None             # partial：不记，下次自动重试


# ── 6. 预算、中止、dry-run、显式 codes、CLI ─────────────────────────────────

def test_aborts_after_consecutive_failures(db, monkeypatch):
    import scripts.backfill_notice_date_quarterly as script
    for i in range(6):
        _save(f"60000{i}", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, script, {}, default_error="ConnectionError: 限流")
    result = script.run(sleep=0, max_consecutive_failures=3)
    assert result["aborted"] is True and result["processed"] == 3
    assert result["status_counts"]["partial"] == 3


def test_time_budget_stops_gracefully(db, monkeypatch):
    import scripts.backfill_notice_date_quarterly as script
    for i in range(5):
        _save(f"60000{i}", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, script, {})
    result = script.run(sleep=0, max_seconds=0.001)
    assert result["budget_hit"] is True and result["processed"] < 5


def test_dry_run_writes_nothing_and_no_progress(db, monkeypatch):
    import scripts.backfill_notice_date_quarterly as script
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, script, {"600519": {"notices": {Q1_2026: "2026-04-25"}}})
    result = script.run(sleep=0, dry_run=True)
    assert result["filled"] == 1 and result["dry_run"] is True
    assert _notices("600519")[Q1_2026] is None
    assert dao.backfill_progress_summary(script.TASK)["done"] == 0
    assert dao.targets_missing_notice_quarterly(task=script.TASK) == [("600519", Q1_2026)]


def test_explicit_codes_bypass_progress(db, monkeypatch):
    import scripts.backfill_notice_date_quarterly as script
    _save("600519", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, script, {"600519": {"notices": {Q1_2026: "2026-04-25"}}})
    script.run(sleep=0)                                   # 先正常跑一次（已排除）
    result = script.run(codes=["600519"], sleep=0)        # 显式点名即必跑
    assert result["targets"] == 1 and result["processed"] == 1
    assert result["remaining"] == 0


def _last_json(capsys):
    """main() 会调 init_database()（既有脚本的同一套做法），stdout 前缀是建表日志，
    JSON 汇总在最后一行。"""
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


def test_cli_status_and_exit_codes(db, monkeypatch, capsys):
    import scripts.backfill_notice_date_quarterly as script
    _save("600519", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, script, {"600519": {"error": "ConnectionError: x"}})
    assert script.main(["--codes", "600519", "--sleep", "0"]) == 1   # partial -> 非 0
    payload = _last_json(capsys)
    assert payload["status_counts"]["partial"] == 1
    assert script.main(["--status"]) == 0
    summary = _last_json(capsys)
    assert summary["partial"] == 1 and summary["done"] == 0


def test_script_module_has_no_chdir():
    """批11 的前置：新脚本只做 sys.path.insert，不改工作目录。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2]
           / "scripts" / "backfill_notice_date_quarterly.py").read_text(encoding="utf-8")
    assert "os.chdir" not in src
    assert "sqlite3" not in src        # 写库全走 DAO，不在脚本里另开一条写入口径
