"""批10-b/10-c 取到率体检回归测试（2026-10-30，只读 + tmp 库）。

核心是**两个口径不许混**：
- 有值率（分母=全部年报行）把"源确认无此项目"和"整份没采到"混在一起；
- 取到率（分母=这份资产负债表确实取到过的行）才是采集质量的度量。
用例把这两个数字分别钉住，并钉住"取到过的行"的宽/严两个判据互不混淆。
"""

import json

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "cov.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _annual(code, date, **fields):
    rec = {"stock_code": code, "report_date": date}
    rec.update(fields)
    return rec


def _save(records):
    db_mod.FinancialHistoryDAO().batch_save(records)


def _seed_shape():
    """形状：10 条年报行 + 1 条季报行。年报里 2 条整份没取到；
    取到的 8 条里 3 条有商誉、6 条有 total_assets、只有 cash / 只有 total_equity
    各 1 条（这两条就是宽判据与严格判据的分歧处）。"""
    rows = [
        # 整份资产负债表没落地（五字段全 NULL）
        _annual("000001", "2025-12-31"),
        _annual("000002", "2024-12-31"),
        # 取到了、有商誉
        _annual("600519", "2025-12-31", cash=1.0, total_assets=9.0, goodwill=2.0),
        _annual("600519", "2024-12-31", cash=1.0, total_assets=9.0, goodwill=3.0),
        _annual("000651", "2025-12-31", cash=1.0, total_assets=9.0, goodwill=4.0),
        # 取到了、源确认无商誉（goodwill 仍 NULL）
        _annual("000651", "2024-12-31", cash=1.0, total_assets=9.0),
        _annual("300750", "2025-12-31", cash=1.0, total_assets=9.0),
        # 只有 cash（宽判据算取到，严格判据 total_assets 不算）
        _annual("300750", "2024-12-31", cash=1.0),
        # 只有 total_equity
        _annual("002415", "2025-12-31", total_equity=5.0),
        _annual("002415", "2024-12-31", cash=1.0, total_assets=9.0),
        # 季报行：不得进分母
        _annual("600519", "2025-03-31", cash=1.0, goodwill=9.0),
    ]
    _save(rows)


def test_two_rates_are_not_confused(db):
    from scripts.report_field_coverage import run
    _seed_shape()
    s = run()
    assert s["annual_rows"] == 10                 # 季报行被排除在分母外
    assert s["fetched_rows"] == 8                 # 宽判据：五字段任一有值
    assert s["strict_fetched_rows"] == 6          # 严格判据：total_assets 有值
    assert s["never_fetched_rows"] == 2
    assert s["goodwill_take_rate_pct"] == 37.5    # 3 / 8 —— 取到率
    assert s["goodwill_has_value_pct_all_rows"] == 30.0   # 3 / 10 —— 有值率


def test_never_fetched_is_the_only_collection_defect(db):
    from scripts.report_field_coverage import run
    _seed_shape()
    s = run()
    assert s["never_fetched_pct"] == 20.0
    codes = {w["code"]: w["row_count"] for w in s["worst_never_fetched_codes"]}
    assert codes == {"000001": 1, "000002": 1}


def test_by_year_groups_only_annual_rows(db):
    from scripts.report_field_coverage import run
    _seed_shape()
    years = {y["year"]: y for y in run()["by_year"]}
    assert years["2025"]["annual_rows"] == 5
    assert years["2024"]["annual_rows"] == 5      # 2025-03-31 那条季报不计入
    assert years["2024"]["goodwill_present"] == 1
    assert years["2025"]["goodwill_present"] == 2
    assert years["2025"]["never_fetched"] == 1


def test_field_table_covers_all_five_fields(db):
    from scripts.report_field_coverage import EXTRA_FIELDS, run
    _seed_shape()
    fields = run()["fields"]
    assert set(fields) == set(EXTRA_FIELDS)
    assert fields["total_assets"]["present"] == 6
    assert fields["cash"]["present"] == 7
    assert fields["total_equity"]["present"] == 1
    assert fields["interest_bearing_debt"]["present"] == 0
    assert fields["interest_bearing_debt"]["take_rate"] == 0.0


def test_empty_table_does_not_divide_by_zero(db):
    from scripts.report_field_coverage import run
    s = run()
    assert s["annual_rows"] == 0
    assert s["goodwill_take_rate_pct"] is None
    assert s["never_fetched_pct"] is None


def test_markdown_output_shows_both_denominators(db):
    from scripts.report_field_coverage import run, to_markdown
    _seed_shape()
    text = to_markdown(run())
    assert "取到率（取到过的行做分母）" in text
    assert "有值率（全部年报行做分母）" in text
    assert "37.5%" in text and "30.0%" in text
    assert "只有这一类是采集缺陷" in text


def test_cli_json_and_md(db, capsys):
    import scripts.report_field_coverage as rep
    _seed_shape()
    assert rep.main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fetched_rows"] == 8
    assert rep.main(["--format", "md"]) == 0
    assert "| 字段 | 有值行数 |" in capsys.readouterr().out


def test_report_never_writes(db):
    from scripts.report_field_coverage import run
    _seed_shape()

    def snapshot():
        with db_mod.db_conn() as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("financial_history", "stock_snapshot")}

    before = snapshot()
    run()
    assert snapshot() == before
