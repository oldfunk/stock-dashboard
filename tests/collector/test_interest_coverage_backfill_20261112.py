"""批19-c 用例（2026-11-12）：interest_coverage 缺口回填（全程 mock，不打网络）。

派工单点名的缺口是 **2026 三个季报报告期**，而既有两条回填路径都写不进这一格：
``update_extra_fields`` 是 A-only 且白名单没有 ic；采集器 ``op/abs(ie)`` 的
``INTEREST_EXPENSE`` 对非金融股恒为空。所以本件走 mainfin ``INTSTCOVRATE``
（库内 34,508/34,509 条既有 ic 与它逐位相同，口径一致）。

守的承诺：
1. 目标集年报 + 季报都算，``covered_report_date`` 取该股最新报告期（新期自动重入选）；
2. 只填 NULL、不覆盖已有值、不造半截行；``ic == 0`` / 负值是合法值必须写进去；
3. 上游解析：四类 REPORT_TYPE 全收、URL 不加类型过滤、空/NaN/inf 不收；
4. 分页语义与季报披露日共用一个 pager：短页即停、上限、后页失败保留前页但带 error；
5. **只填 ic**，不顺带 fcf/roic（fcf 有两种口径，混列会毁掉历史序列）；
6. 进度表三态、连续失败中止、时间预算、dry-run 零写入、显式 codes 必跑、CLI 出口码；
7. 逻辑在 src（周六维护要复用）、脚本只做薄 CLI 且不 chdir/不另开写库口径。
"""

import json
import re
from pathlib import Path

import pytest

from src.models import database as db_mod

Q1_2026 = "2026-03-31"
Q2_2026 = "2026-06-30"
Q3_2026 = "2026-09-30"
ANNUAL_2025 = "2025-12-31"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "ic.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _save(code, pairs):
    """pairs: [(report_date, ic 或 None)]，走既有 batch_save 口径。"""
    db_mod.FinancialHistoryDAO().batch_save([
        {"stock_code": code, "report_date": d, "interest_coverage": ic, "roe": 10.0}
        for d, ic in pairs])


def _ic(code):
    with db_mod.db_conn() as conn:
        return {r["report_date"]: r["interest_coverage"] for r in conn.execute(
            "SELECT report_date, interest_coverage FROM financial_history"
            " WHERE stock_code = ?", (code,))}


def _patch_fetch(monkeypatch, module, by_code, default_error=None):
    """by_code: {code: {"values": {...}, "error": str|None, "rows": int}}。"""

    def fake_fetch(code, ps=100, timeout=15, max_pages=3):
        payload = by_code.get(code, {})
        return {"values": payload.get("values", {}),
                "rows": payload.get("rows", 0),
                "error": payload.get("error") or default_error}

    monkeypatch.setattr(module, "fetch_mainfin_interest_coverage", fake_fetch)


# ── 1. 目标集：年报 + 季报都算 ─────────────────────────────────────────────

def test_targets_cover_both_annual_and_quarterly_gaps(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None), (ANNUAL_2025, 12.0)])   # 只有季报缺
    _save("000001", [(Q3_2026, 8.0), (ANNUAL_2025, None)])    # 只有年报缺
    _save("600036", [(Q3_2026, 5.0), (ANNUAL_2025, 6.0)])    # 齐全 -> 不入选
    assert dao.targets_missing_interest_coverage() == [
        ("000001", Q3_2026), ("600519", Q3_2026)]


def test_latest_report_date_spans_both_report_types(db):
    """covered 要取 A+Q 的最大值：只取年报会把新季报永久锁在进度表外。"""
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(ANNUAL_2025, None), (Q3_2026, None)])
    assert dao.targets_missing_interest_coverage() == [("600519", Q3_2026)]


def test_targets_limit_offset_and_order(db):
    dao = db_mod.FinancialHistoryDAO()
    for i in range(5):
        _save(f"60000{i}", [(Q3_2026, None)])
    assert dao.targets_missing_interest_coverage(limit=2) == [
        ("600000", Q3_2026), ("600001", Q3_2026)]
    assert dao.targets_missing_interest_coverage(limit=2, offset=2) == [
        ("600002", Q3_2026), ("600003", Q3_2026)]


# ── 2. 写入口径 ────────────────────────────────────────────────────────────

def test_fill_touches_only_null_rows_in_both_report_types(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None),          # 季报空 -> 填
                     (Q2_2026, 7.5),           # 季报已有值 -> 不覆盖
                     (ANNUAL_2025, None)])     # 年报空 -> 也填（本件不限 A/Q）
    filled = dao.fill_interest_coverage("600519", {
        Q3_2026: 9.0, Q2_2026: 99.0, ANNUAL_2025: 12.0})
    assert filled == 2
    assert _ic("600519") == {Q3_2026: 9.0, Q2_2026: 7.5, ANNUAL_2025: 12.0}


@pytest.mark.parametrize("value", [0.0, -3.5])
def test_zero_and_negative_are_legal_values(db, value):
    """营业利润为零/为负时 ic 就是 0 或负数——用真值判空会把它们静默丢掉（N21）。"""
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None)])
    assert dao.fill_interest_coverage("600519", {Q3_2026: value}) == 1
    assert _ic("600519")[Q3_2026] == value


def test_none_value_is_skipped(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None)])
    assert dao.fill_interest_coverage("600519", {Q3_2026: None}) == 0
    assert _ic("600519")[Q3_2026] is None


def test_unknown_report_date_creates_no_rows(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None)])
    filled = dao.fill_interest_coverage(
        "600519", {"2019-03-31": 4.0, Q3_2026: 9.0})
    assert filled == 1
    assert set(_ic("600519")) == {Q3_2026}


def test_dry_run_uses_same_predicate_and_writes_nothing(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None), (Q2_2026, 7.5)])
    predicted = dao.fill_interest_coverage(
        "600519", {Q3_2026: 9.0, Q2_2026: 99.0}, dry_run=True)
    assert predicted == 1
    assert _ic("600519")[Q3_2026] is None


def test_rerun_is_idempotent(db):
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None)])
    assert dao.fill_interest_coverage("600519", {Q3_2026: 9.0}) == 1
    assert dao.fill_interest_coverage("600519", {Q3_2026: 123.0}) == 0
    assert _ic("600519")[Q3_2026] == 9.0


# ── 3. 上游解析 ────────────────────────────────────────────────────────────

def _row(kind, date, ic, **extra):
    return {"REPORT_TYPE": kind, "REPORT_DATE": f"{date} 00:00:00",
            "INTSTCOVRATE": ic, **extra}


def test_fetch_ic_covers_all_four_report_types(monkeypatch):
    from src.collector import mainfin_fetcher as mf
    seen = {}

    def fake_get_json(url, timeout=15, headers=None):
        seen["url"] = url
        return {"result": {"data": [
            _row("年报", ANNUAL_2025, 12.34),
            _row("一季报", Q1_2026, 8.5),
            _row("中报", Q2_2026, 9.25),
            _row("三季报", Q3_2026, 10.75),
        ]}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    out = mf.fetch_mainfin_interest_coverage("600519")
    assert out["values"] == {ANNUAL_2025: 12.34, Q1_2026: 8.5,
                             Q2_2026: 9.25, Q3_2026: 10.75}
    assert out["rows"] == 4 and out["error"] is None
    # 东财没有 '季报' 这个取值：加类型过滤会稳定 0 行（批10-a 取证）
    assert "REPORT_TYPE" not in seen["url"]
    assert "ps=100" in seen["url"] and "600519.SH" in seen["url"]


@pytest.mark.parametrize("bad", [None, "", "--", "NaN", "inf", "-inf"])
def test_fetch_ic_skips_unusable_values(monkeypatch, bad):
    from src.collector import mainfin_fetcher as mf
    monkeypatch.setattr(mf, "_get_json", lambda url, timeout=15, headers=None: {
        "result": {"data": [_row("三季报", Q3_2026, bad),
                            _row("中报", Q2_2026, 9.0)]}})
    out = mf.fetch_mainfin_interest_coverage("600519")
    assert out["values"] == {Q2_2026: 9.0}


def test_fetch_ic_skips_empty_report_date(monkeypatch):
    from src.collector import mainfin_fetcher as mf
    monkeypatch.setattr(mf, "_get_json", lambda url, timeout=15, headers=None: {
        "result": {"data": [{"REPORT_TYPE": "年报", "REPORT_DATE": None,
                             "INTSTCOVRATE": 5.0}]}})
    assert mf.fetch_mainfin_interest_coverage("600519")["values"] == {}


def test_fetch_ic_only_maps_interest_coverage(monkeypatch):
    """只填 ic：同一响应里的 FCFF_BACK/ROIC 一律不取（fcf 有两种口径，不能混列）。"""
    from src.collector import mainfin_fetcher as mf
    monkeypatch.setattr(mf, "_get_json", lambda url, timeout=15, headers=None: {
        "result": {"data": [_row("三季报", Q3_2026, None,
                                 FCFF_BACK=1e8, ROIC=7.0, TOTAL_SHARE=2e6)]}})
    out = mf.fetch_mainfin_interest_coverage("600519")
    assert out["values"] == {}
    assert set(out) == {"values", "rows", "error"}


def test_fetch_ic_network_error_is_not_empty_result(monkeypatch):
    """网络失败必须带 error：当成"这只票没有 ic"会让续传永久跳过它。"""
    from src.collector import mainfin_fetcher as mf

    def boom(url, timeout=15, headers=None):
        raise TimeoutError("上游无响应")

    monkeypatch.setattr(mf, "_get_json", boom)
    out = mf.fetch_mainfin_interest_coverage("600519")
    assert out["values"] == {} and "TimeoutError" in out["error"]


def test_fetch_ic_bj_code_uses_bj_secucode(monkeypatch):
    from src.collector import mainfin_fetcher as mf
    seen = {}

    def fake_get_json(url, timeout=15, headers=None):
        seen["url"] = url
        return {"result": {"data": []}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    mf.fetch_mainfin_interest_coverage("920000")
    assert "920000.BJ" in seen["url"]


def _page(ps, first_year):
    rows = []
    for i in range(ps):
        year = first_year - i // 4
        kind = ("年报", "一季报", "中报", "三季报")[i % 4]
        month = ("12", "03", "06", "09")[i % 4]
        rows.append(_row(kind, f"{year}-{month}-28", 10.0 + i))
    return rows


def test_fetch_ic_pages_until_short_page(monkeypatch):
    from src.collector import mainfin_fetcher as mf
    pages = []

    def fake_get_json(url, timeout=15, headers=None):
        page = int(re.search(r"[&?]p=(\d+)", url).group(1))
        pages.append(page)
        return {"result": {"data": _page(100, 2026) if page == 1 else _page(3, 2001)}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    out = mf.fetch_mainfin_interest_coverage("000001")
    assert pages == [1, 2] and out["rows"] == 103
    assert any(d.startswith("2001") for d in out["values"])


def test_fetch_ic_respects_max_pages(monkeypatch):
    from src.collector import mainfin_fetcher as mf
    pages = []

    def fake_get_json(url, timeout=15, headers=None):
        page = int(re.search(r"[&?]p=(\d+)", url).group(1))
        pages.append(page)
        return {"result": {"data": _page(100, 2026 - page)}}

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    mf.fetch_mainfin_interest_coverage("000001")
    assert pages == [1, 2, 3]


def test_fetch_ic_later_page_failure_keeps_earlier_and_reports_error(monkeypatch):
    from src.collector import mainfin_fetcher as mf

    def fake_get_json(url, timeout=15, headers=None):
        page = int(re.search(r"[&?]p=(\d+)", url).group(1))
        if page == 1:
            return {"result": {"data": _page(100, 2026)}}
        raise TimeoutError("第 2 页无响应")

    monkeypatch.setattr(mf, "_get_json", fake_get_json)
    out = mf.fetch_mainfin_interest_coverage("000001")
    assert out["values"] and out["rows"] == 100
    assert "TimeoutError" in out["error"]


# ── 4. run / 进度表语义 ────────────────────────────────────────────────────

def test_run_records_progress_and_resumes(db, monkeypatch):
    from src.collector import interest_coverage_backfill as mod
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None)])
    _save("000001", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {
        "600519": {"values": {Q3_2026: 9.0}},
        "000001": {"error": "ConnectionError: 限流"},
    })
    result = mod.run(sleep=0)
    assert result["filled"] == 1
    assert result["status_counts"] == {"done": 1, "partial": 1, "no_data": 0}
    assert result["errors"] == [["000001", "ConnectionError: 限流"]]
    assert _ic("600519")[Q3_2026] == 9.0
    assert dao.backfill_progress_summary(mod.TASK)["done"] == 1
    assert dao.targets_missing_interest_coverage(task=mod.TASK) == [("000001", Q3_2026)]

    _patch_fetch(monkeypatch, mod, {"000001": {"values": {Q3_2026: 4.0}}})
    again = mod.run(sleep=0)
    assert again["targets"] == 1 and again["filled"] == 1 and again["remaining"] == 0


def test_new_report_period_reenters_target_set(db, monkeypatch):
    from src.collector import interest_coverage_backfill as mod
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q1_2026, None)])
    _patch_fetch(monkeypatch, mod, {"600519": {"values": {Q1_2026: 9.0}}})
    mod.run(sleep=0)
    assert dao.targets_missing_interest_coverage(task=mod.TASK) == []
    _save("600519", [(Q3_2026, None)])
    assert dao.targets_missing_interest_coverage(
        task=mod.TASK) == [("600519", Q3_2026)]


def test_no_data_marks_covered_but_partial_does_not(db, monkeypatch):
    from src.collector import interest_coverage_backfill as mod
    _save("600519", [(Q3_2026, None)])
    _save("000001", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {
        "600519": {"values": {}, "rows": 30},
        "000001": {"values": {}, "error": "ValueError: 上游空返回"},
    })
    mod.run(sleep=0)
    with db_mod.db_conn() as conn:
        covered = dict(conn.execute(
            "SELECT code, covered_report_date FROM backfill_progress"
            " WHERE task = ?", (mod.TASK,)).fetchall())
    assert covered["600519"] == Q3_2026
    assert covered["000001"] is None


def test_partial_keeps_whatever_earlier_pages_returned(db, monkeypatch):
    """后页失败：已取到的 ic 照样写库，但状态是 partial（下次重试补齐）。"""
    from src.collector import interest_coverage_backfill as mod
    _save("600519", [(Q3_2026, None), (ANNUAL_2025, None)])
    _patch_fetch(monkeypatch, mod, {
        "600519": {"values": {Q3_2026: 9.0}, "error": "TimeoutError: 第 2 页"}})
    result = mod.run(sleep=0)
    assert result["filled"] == 1 and result["status_counts"]["partial"] == 1
    assert _ic("600519") == {Q3_2026: 9.0, ANNUAL_2025: None}


def test_aborts_after_consecutive_failures(db, monkeypatch):
    from src.collector import interest_coverage_backfill as mod
    for i in range(6):
        _save(f"60000{i}", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {}, default_error="ConnectionError: 限流")
    result = mod.run(sleep=0, max_consecutive_failures=3)
    assert result["aborted"] is True and result["processed"] == 3
    assert result["status_counts"]["partial"] == 3


def test_time_budget_stops_gracefully(db, monkeypatch):
    from src.collector import interest_coverage_backfill as mod
    for i in range(5):
        _save(f"60000{i}", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {})
    result = mod.run(sleep=0, max_seconds=0.001)
    assert result["budget_hit"] is True and result["processed"] < 5


def test_dry_run_writes_nothing_and_no_progress(db, monkeypatch):
    from src.collector import interest_coverage_backfill as mod
    dao = db_mod.FinancialHistoryDAO()
    _save("600519", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {"600519": {"values": {Q3_2026: 9.0}}})
    result = mod.run(sleep=0, dry_run=True)
    assert result["filled"] == 1 and result["dry_run"] is True
    assert _ic("600519")[Q3_2026] is None
    assert dao.backfill_progress_summary(mod.TASK)["done"] == 0
    assert dao.targets_missing_interest_coverage(task=mod.TASK) == [("600519", Q3_2026)]


def test_explicit_codes_bypass_progress(db, monkeypatch):
    from src.collector import interest_coverage_backfill as mod
    _save("600519", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {"600519": {"values": {Q3_2026: 9.0}}})
    mod.run(sleep=0)
    result = mod.run(codes=["600519"], sleep=0)
    assert result["targets"] == 1 and result["processed"] == 1
    assert result["remaining"] == 0


def test_force_ignores_progress_marks(db, monkeypatch):
    from src.collector import interest_coverage_backfill as mod
    _save("600519", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {"600519": {"values": {}}})   # no_data 也会被排除
    mod.run(sleep=0)
    assert mod.run(sleep=0)["targets"] == 0
    assert mod.run(sleep=0, force=True)["targets"] == 1


def test_summary_reports_elapsed_for_pacing(db, monkeypatch):
    """派工单要"测速"：耗时必须能被读到，否则批大小只能靠猜。"""
    from src.collector import interest_coverage_backfill as mod
    _save("600519", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {"600519": {"values": {Q3_2026: 9.0}, "rows": 4}})
    logged = []

    class Spy:
        def info(self, msg, *a):
            logged.append(msg % a if a else str(msg))

        warning = error = debug = info

    monkeypatch.setattr(mod, "logger", Spy())
    result = mod.run(sleep=0)
    assert result["elapsed_seconds"] >= 0 and result["rows"] == 4
    assert any("耗时" in m and "剩余" in m for m in logged)


# ── 5. CLI ─────────────────────────────────────────────────────────────────

def _last_json(capsys):
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


def test_cli_status_and_exit_codes(db, monkeypatch, capsys):
    import scripts.backfill_interest_coverage as script
    from src.collector import interest_coverage_backfill as mod
    _save("600519", [(Q3_2026, None)])
    _patch_fetch(monkeypatch, mod, {"600519": {"error": "ConnectionError: x"}})
    assert script.main(["--codes", "600519", "--sleep", "0"]) == 1
    assert _last_json(capsys)["status_counts"]["partial"] == 1
    assert script.main(["--status"]) == 0
    summary = _last_json(capsys)
    assert summary["partial"] == 1 and summary["done"] == 0


def test_cli_limit_and_force_are_wired(db, monkeypatch, capsys):
    """CLI 上的旗标必须真的传到 run()：留着不接的旗标就是死开关。"""
    import scripts.backfill_interest_coverage as script
    from src.collector import interest_coverage_backfill as mod
    for i in range(3):
        _save(f"60000{i}", [(Q3_2026, None)])
    # 600002 上游没给 ic -> 仍留 NULL，但会被 no_data 标记排除；--force 才重新入选
    _patch_fetch(monkeypatch, mod, {
        "600000": {"values": {Q3_2026: 9.0}},
        "600001": {"values": {Q3_2026: 9.0}},
        "600002": {"values": {}},
    })
    assert script.main(["--limit", "1", "--sleep", "0"]) == 0
    assert _last_json(capsys)["processed"] == 1
    assert script.main(["--sleep", "0"]) == 0
    assert _last_json(capsys)["processed"] == 2
    assert script.main(["--sleep", "0"]) == 0
    assert _last_json(capsys)["targets"] == 0
    assert script.main(["--sleep", "0", "--force", "--dry-run"]) == 0
    payload = _last_json(capsys)
    assert payload["targets"] == 1 and payload["dry_run"] is True


def test_script_is_a_thin_cli(db):
    """逻辑在 src（周六维护要复用），脚本不 chdir、不另开写库口径。"""
    root = Path(__file__).resolve().parents[2]
    src = (root / "scripts" / "backfill_interest_coverage.py").read_text(encoding="utf-8")
    assert "os.chdir" not in src
    assert "sqlite3" not in src
    assert "from src.collector.interest_coverage_backfill import" in src
    mod_src = (root / "src" / "collector"
               / "interest_coverage_backfill.py").read_text(encoding="utf-8")
    assert "import scripts" not in mod_src