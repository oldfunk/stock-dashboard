"""15-e（批15）用例：H-001 regime 混杂检查工具。

这个脚本要回答的唯一问题是"17pp 里有多少是 MOS、多少是 risk_off 环境基线"，
所以用例的关键是**构造一个 H-001 形状的假数据**：≥40 档只在 risk_off 出现，
且 risk_off 内部它与 <=0 档几乎一样——此时全样本口径会虚报"MOS 有效"，
而工具必须把环境基线扣掉后给出接近 0 的差值。这一条测的是判据方向，
不是格式化输出。
"""

import sqlite3

import pytest

from scripts.h001_regime_check import (
    analyze, bootstrap_diff_ci, load_rows, main, maturity_by_regime, open_read_only,
    tier_composition, tier_of, tier_regime_means, to_markdown, within_regime_diffs,
)


def _row(stock, day, mos, e20, e60, regime):
    return {"stock_code": stock, "analysis_date": day, "mos_at_entry": mos,
            "excess_20": e20, "excess_60": e60, "regime": regime}


# ── 纯函数 ────────────────────────────────────────────────

@pytest.mark.parametrize("mos,expect", [
    (None, None), (-523.7, "<=0"), (0.0, "0~20"), (19.99, "0~20"),
    (20.0, "20~40"), (39.9, "20~40"), (40.0, ">=40"), (74.8, ">=40")])
def test_tier_bounds_match_the_experiment(mos, expect):
    """档位边界必须与 experiment._tier_metrics 的默认档一致（<=0 / 0~20 / 20~40 / >=40）。"""
    assert tier_of(mos) == expect


def test_confounding_is_detected_when_only_one_regime_matures():
    rows = [_row("A", "2026-07-01", -10, 1.0, 9.0, "risk_off"),
            _row("B", "2026-07-02", 50, 2.0, 9.5, "risk_off"),
            _row("C", "2026-09-01", -10, 0.5, None, "neutral")]
    res = analyze(rows, n_boot=200)
    assert res["horizons"]["excess_60"]["confounded"] is True
    assert res["horizons"]["excess_60"]["mature_rows_outside_risk_off"] == 0
    # T+20 有 neutral 成熟行 → 不算共线
    assert res["horizons"]["excess_20"]["confounded"] is False


def test_unlabeled_rows_are_not_counted_as_a_control_group():
    """无 regime 标签的行既不是 risk_off 也不能当对照——拿它判"不共线"是自欺。

    （产线现况：T+60 成熟 148 行 = risk_off 147 + 无标签 1，
    若把无标签算作对照，就会得到"还有对照可用"的错误结论。）
    """
    rows = [_row("A", "d1", -10, None, 9.0, "risk_off"),
            _row("B", "d2", 50, None, 9.5, "risk_off"),
            _row("C", "d3", 50, None, 8.0, None)]
    h = analyze(rows, n_boot=200)["horizons"]["excess_60"]
    assert h["mature_rows_unlabeled"] == 1
    assert h["mature_rows_outside_risk_off"] == 0
    assert h["confounded"] is True


def test_environment_baseline_is_removed_before_crediting_mos():
    """H-001 形状：≥40 档只存在于 risk_off，且在 risk_off 内与基线档几乎持平。

    全样本看：>=40 均值 10.5 vs <=0 均值 4.5 → 像"6pp 的 MOS 效应"；
    扣掉环境后只剩 0.5pp。工具必须报后者，否则就是替环境暴露背书。
    """
    rows = []
    for i in range(6):
        rows.append(_row(f"S{i}", f"2026-07-{10 + i:02d}", -10, 10.0, 10.0, "risk_off"))
        rows.append(_row(f"T{i}", f"2026-07-{10 + i:02d}", 50, 10.5, 10.5, "risk_off"))
        rows.append(_row(f"U{i}", f"2026-09-{10 + i:02d}", -10, -1.0, None, "neutral"))
    grid = {(g["regime"], g["tier"]): g["mean_pp"] for g in tier_regime_means(rows, "excess_20")}
    assert grid[("risk_off", "<=0")] == 10.0
    assert grid[("risk_off", ">=40")] == 10.5
    assert grid[("neutral", "<=0")] == -1.0

    within = {w["tier"]: w for w in
              analyze(rows, n_boot=500)["horizons"]["excess_20"]
              ["within_risk_off_vs_base"]}
    hi = within[">=40"]
    assert hi["diff_pp"] == pytest.approx(0.5, abs=1e-6)
    # 全样本口径（不做同 regime 对照）会给出 6pp——这正是首跑数字的来源
    all_hi = [r for r in rows if tier_of(r["mos_at_entry"]) == ">=40"]
    all_lo = [r for r in rows if tier_of(r["mos_at_entry"]) == "<=0"]
    naive = (sum(r["excess_20"] for r in all_hi) / len(all_hi)
             - sum(r["excess_20"] for r in all_lo) / len(all_lo))
    assert naive == pytest.approx(6.0, abs=1e-9)
    assert abs(hi["diff_pp"]) < naive / 5, "扣掉环境基线后必须显著变小，否则判据没生效"


def test_clear_separation_is_labelled_significant():
    """真差 20pp、每股单行（自助无波动）→ CI 收到 20pp 一点，必须判"不含 0"。

    另一侧的判据（差值 ≈0 时不判显著）由上一条用 diff 幅度守住：
    判据本身不掺"越大越显著"的运气成分。
    """
    cur = [_row(f"C{i}", f"d{i}", 50, 30.0, 30.0, "risk_off") for i in range(6)]
    base = [_row(f"B{i}", f"d{i}", -10, 10.0, 10.0, "risk_off") for i in range(6)]
    d, lo, hi = bootstrap_diff_ci(cur, base, "excess_20", n_boot=300, seed=3)
    assert (d, lo, hi) == (20.0, 20.0, 20.0)
    w = within_regime_diffs(cur + base, "excess_20", "risk_off", 300, 3)
    assert w[2]["tier"] == ">=40" and w[2]["verdict"] == "CI 不含 0"


def test_maturity_by_regime_counts_rows_only():
    rows = [_row("A", "d1", -1, 1.0, None, "risk_off"),
            _row("B", "d2", 50, 2.0, 3.0, "risk_off"),
            _row("C", "d3", 50, 4.0, 5.0, None)]
    assert maturity_by_regime(rows, "excess_20") == {"risk_off": 2, "无标签": 1}
    assert maturity_by_regime(rows, "excess_60") == {"risk_off": 1, "无标签": 1}


def test_tier_composition_reports_zero_stock_overlap():
    """两档股票零重叠时，档间差里混着"不同的公司"——必须显式暴露出来。"""
    rows = [_row("A", "d1", -10, 1.0, 1.0, "risk_off"),
            _row("B", "d2", 50, 2.0, 2.0, "risk_off")]
    comp = tier_composition(rows, ">=40")
    assert comp["rows"] == 1 and comp["stocks"] == 1
    assert comp["overlap_with_base"] == []


def test_bootstrap_diff_is_deterministic_per_seed():
    cur = [_row(f"S{i}", f"d{i}", 50, 5.0 + (i % 2), 5.0, "risk_off")
           for i in range(8)]
    base = [_row(f"B{i}", f"d{i}", -10, 4.0 + (i % 2), 4.0, "risk_off")
            for i in range(8)]
    a = bootstrap_diff_ci(cur, base, "excess_20", n_boot=400, seed=7)
    b = bootstrap_diff_ci(cur, base, "excess_20", n_boot=400, seed=7)
    assert a == b, "同一 seed 必须完全可复现（实验的可审计性靠这个）"
    diff, lo, hi = a
    assert diff == pytest.approx(1.0)
    assert lo <= 1.0 <= hi


def test_empty_group_gives_nan_instead_of_crashing():
    """缺一侧样本时返回 NaN（宁可没有数，也不许拿单侧均值当"差值"）。"""
    d, lo, hi = bootstrap_diff_ci([_row("A", "d", 50, 1, 1, "risk_off")], [], "excess_20")
    assert d != d and lo != lo, f"空基线必须给 NaN，实得 {(d, lo, hi)}"


# ── 只读保证与 CLI ────────────────────────────────────────

def _seed_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE analysis_outcome (
            analysis_id TEXT, benchmark_code TEXT, stock_code TEXT,
            analysis_date TEXT, mos_at_entry REAL, excess_20 REAL, excess_60 REAL);
        CREATE TABLE market_state_daily (trade_date TEXT, regime TEXT);
    """)
    data = [("x1", "sh000300", "A", "2026-07-01", -10, 1.0, 9.0),
            ("x2", "sh000300", "B", "2026-07-02", 50, 2.0, 9.5),
            ("x3", "sh000922", "A", "2026-07-01", -10, 5.0, 5.0)]
    conn.executemany("INSERT INTO analysis_outcome VALUES (?,?,?,?,?,?,?)", data)
    conn.executemany("INSERT INTO market_state_daily VALUES (?,?)",
                     [("2026-07-01", "risk_off"), ("2026-07-02", "risk_off")])
    conn.commit()
    conn.close()


def test_read_only_connection_rejects_writes(tmp_path):
    _seed_db(tmp_path / "h.db")
    conn = open_read_only(str(tmp_path / "h.db"))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM analysis_outcome")
    conn.close()


def test_load_rows_filters_benchmark_and_keeps_missing_regime(tmp_path):
    _seed_db(tmp_path / "h.db")
    conn = open_read_only(str(tmp_path / "h.db"))
    try:
        rows = load_rows(conn, "sh000300")
    finally:
        conn.close()
    assert len(rows) == 2 and rows[0]["regime"] == "risk_off"


def test_cli_md_output_and_no_chdir(tmp_path, capsys):
    """只读脚本不许 chdir（否则调度器/CI 的工作目录被改写），md 输出要带共线结论。"""
    import inspect
    src = inspect.getsource(__import__("scripts.h001_regime_check",
                                       fromlist=["x"]))
    assert "chdir" not in src
    _seed_db(tmp_path / "h.db")
    assert main(["--db", str(tmp_path / "h.db"), "--format", "md",
                 "--n-boot", "200"]) == 0
    out = capsys.readouterr().out
    assert "完全共线" in out and "excess_60" in out
