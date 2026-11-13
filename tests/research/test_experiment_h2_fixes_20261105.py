"""批16-b 用例（2026-11-05）：Hermes H2 四点研究域修复。

E-H001 / E-H001b 是**已归档的不可改记录**，本批改的是未来实验的分域正确性：
- P1-H2-1 分域用最新快照 → 前视 + 幸存者偏差（改 PIT）；
- P1-H2-2 缺标签行被 str(None) 混进桶里、分域均值不带样本数（改 unknown 桶 +
  每桶 {mean, n} + notes 覆盖率）；
- P2-H2-1 holdout 与 regime join 用字符串比较日期（带时间部分的 analysis_date
  会分错侧 / join 不上）；
- P2-H2-2 notes 的"样本 X / 全市场 Y"读起来像覆盖率，其实是两个不同集合。
"""

import pytest

from src.models import database as db_mod

COLS = ("analysis_id, benchmark_code, stock_code, analysis_date, entry_date, "
        "signal, confidence, verdict, entry_price, ret_20, ret_60, ret_120, "
        "bench_20, bench_60, bench_120, excess_20, excess_60, excess_120, "
        "mos_at_entry, updated_at")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "h2.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _insert(rows, benchmark="sh000300", start_id=1):
    """rows: (stock, analysis_date, mos, e20, e60)；日期允许带时间部分/脏值。"""
    with db_mod.db_conn() as conn:
        for i, (stock, day, mos, e20, e60) in enumerate(rows, start=start_id):
            conn.execute(
                f"INSERT INTO analysis_outcome ({COLS}) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i, benchmark, stock, day, day, "BUY", "高", "通过", 1.0,
                 0.01, 0.02, None, 0.0, 0.0, None, e20, e60, None, mos,
                 "2026-11-05"))


def _snap(code, snapshot_date, market_cap, sector="白酒"):
    db_mod.StockSnapshotDAO().save_batch([{
        "code": code, "name": code, "market": "A", "sector": sector,
        "pe": 20.0, "pb": 3.0, "ps": 1.0, "market_cap": market_cap,
        "circulating_cap": market_cap, "roe": 10.0, "revenue": 1.0,
        "revenue_growth": 5.0, "profit": 1.0, "profit_growth": 5.0,
        "debt_ratio": 30.0, "current_price": 1.0, "high_52w": 1.0,
        "low_52w": 1.0, "is_st": False, "list_date": "2010-01-01",
        "snapshot_date": snapshot_date}])


def _plan(splits=("sector", "size"), holdout_days=0, start="2026-07-08",
          end="2026-07-08"):
    from src.research.experiment import ExperimentPlan
    return ExperimentPlan(
        experiment_id="E-H2", hypothesis_id="H-2", data_snapshot_id="DS-H2",
        dataset="analysis_outcome", universe="A", benchmark="sh000300",
        metric="excess_20_20~40", threshold=">0", horizon_days=20,
        regimes=["all"], costs={}, random_seed=42, code_commit="c",
        config_hash="h",
        params={"start_date": start, "end_date": end, "horizons": [20],
                "n_boot": 50, "splits": list(splits),
                "holdout_days": holdout_days})


def _run(plan):
    from src.research.experiment import OutcomeEventStudyRunner
    from src.research.snapshot import create_snapshot
    return OutcomeEventStudyRunner().run(
        plan, create_snapshot(["analysis_outcome"], snapshot_id="DS-H2"))


def _metric(outcome, name="excess_20_20~40"):
    return {r.metric: r for r in outcome.results}[name]


# ── P1-H2-1：分域按分析日取快照（PIT），不取"最新" ──────────

def test_snapshot_only_on_or_before_the_analysis_date_is_usable(db):
    """只有**不晚于分析日**的快照能进分域。

    `stock_snapshot.code` 是 PRIMARY KEY、save_batch 用 INSERT OR REPLACE，一码
    只有一行，所以这里用两只票把两个方向都钉住：
    - A 快照 2026-07-08（=分析日）→ 正常分档 small；
    - B 快照 2026-11-01（分析日之后）→ 旧实现拿它把 7 月的分析判成 large，
      PIT 口径下这条标签当时还不存在 → unknown。
    """
    _snap("A", "2026-07-08", 5.0)
    _snap("B", "2026-11-01", 200.0)
    _insert([("A", "2026-07-08", 30.0, 0.03, 0.05),
             ("B", "2026-07-08", 30.0, 0.09, 0.05)])
    m = _metric(_run(_plan(splits=("size",))))
    assert set(m.splits["size"]) == {"small", "unknown"}, \
        "前视：分析日之后才有的市值被用来给该分析分档"
    assert m.splits["size"]["small"] == {"mean": 0.03, "n": 1}
    assert m.splits["size"]["unknown"] == {"mean": 0.09, "n": 1}


def test_stock_snapshot_is_one_row_per_code_so_pit_history_is_missing(db):
    """钉住 PIT 的**前提约束**：stock_snapshot 一码一行，同码换日期是覆盖不是追加。

    产线只读实测（2026-11-05）：5527 行 / 5527 个 code、snapshot_date 只有 51 个
    去重值 → 历史分析日普遍没有当日快照可 join，780 行分析在 PIT 口径下只命中
    2 行。这条用例不是给分域加分，是把"补快照历史表之前 sector/size 只能读
    unknown"写成可执行的记录：哪天 stock_snapshot 改成按日追加，它会红，届时
    notes 的免责文案和 16-b 的跨域提请要一起撤销。
    """
    _snap("A", "2026-07-08", 5e8)
    _snap("A", "2026-11-01", 2e10)          # 同码后写覆盖前写，不产生历史
    with db_mod.db_conn() as conn:
        rows = conn.execute(
            "SELECT snapshot_date, market_cap FROM stock_snapshot"
            " WHERE code='A' ORDER BY snapshot_date").fetchall()
        assert [(r["snapshot_date"], r["market_cap"]) for r in rows] == [
            ("2026-11-01", 2e10)]
        pk = [r["name"] for r in conn.execute("PRAGMA table_info(stock_snapshot)")
              if r["pk"]]
        assert pk == ["code"], "stock_snapshot 主键变了 → PIT 口径要重算"
    _insert([("A", "2026-07-08", 30.0, 0.03, 0.05)])
    outcome = _run(_plan(splits=("size",)))
    assert set(_metric(outcome).splits["size"]) == {"unknown"}
    assert "一码一行" in outcome.notes      # 覆盖率低的原因要在 notes 里说清


def test_size_bucket_is_not_invented_when_the_snapshot_has_no_cap(db):
    """取不到市值 = None = unknown 桶，不许退化成 small。

    旧写法把 cap 缺失当 0 一路落到 else，于是"没有数据"归进小盘组，而小盘组
    正是这类实验里最容易出高收益的桶——缺失被算进了结论方向有利的一侧。
    """
    _snap("A", "2026-07-08", None)          # 快照在、市值空
    _insert([("A", "2026-07-08", 30.0, 0.03, 0.05)])
    m = _metric(_run(_plan(splits=("size",))))
    assert set(m.splits["size"]) == {"unknown"}
    assert m.splits["size"]["unknown"]["n"] == 1


@pytest.mark.parametrize("cap,expect", [
    (None, None), (0, None), (-1.0, None), (50.0, "small"),
    (100.0, "mid"), (999.0, "mid"), (1000.0, "large"), (31418.21, "large")])
def test_size_bucket_never_invents_a_class(cap, expect):
    """单位是**亿元**（2026-11-13 裁定 2）：与 stock_snapshot.market_cap 同源。

    旧夹具写的是元（5e8/1e9/1e10），产线写入侧却是亿元 → 5,217 只全落 small。
    夹具必须跟着改，否则"测试绿"只说明夹具自洽、不说明产线对。
    """
    from src.research.experiment import _size_bucket
    assert _size_bucket(cap) == expect


# ── P1-H2-2：None 桶显式化 + 每桶样本数 + notes 覆盖率 ────

def test_missing_regime_becomes_an_explicit_unknown_bucket(db):
    """market_state 缺当日行 → 桶名 `unknown`（旧实现是字符串 "None"，报告层易整桶忽略）。"""
    _insert([("A", "2026-07-08", 30.0, 0.03, 0.05),
             ("B", "2026-07-09", 30.0, 0.09, 0.05)])   # 07-09 没有 market_state 行
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO market_state_daily (trade_date, status, regime,"
                     " created_at) VALUES ('2026-07-08','pass','risk_off',"
                     "'2026-11-05T00:00:00')")
    outcome = _run(_plan(splits=("regime",), start="2026-07-01", end="2026-07-10"))
    m = _metric(outcome)
    assert "None" not in m.splits["regime"]
    assert set(m.splits["regime"]) == {"risk_off", "unknown"}
    assert m.splits["regime"]["risk_off"] == {"mean": 0.03, "n": 1}
    assert m.splits["regime"]["unknown"] == {"mean": 0.09, "n": 1}, \
        "每桶必须带样本数，否则首跑那种 17pp 全来自 risk_off 读不出是几行撑的"
    assert "分域覆盖率：regime 1/2" in outcome.notes


def test_regime_join_truncates_the_date_not_compares_whole_string(db):
    """P2-H2-1 的连带面：analysis_date 带时间部分时 regime 整列 join 不上。

    `trade_date = '2026-07-08 09:30'` 恒假 → 全部行落 unknown，regime 分域静默
    失效（产线 780 行还没有时间部分，但写入格式一变就整列消失，而且看起来像
    "那些天没有 regime"）。
    """
    _insert([("A", "2026-07-08 09:30", 30.0, 0.03, 0.05)])
    with db_mod.db_conn() as conn:
        conn.execute("INSERT INTO market_state_daily (trade_date, status, regime,"
                     " created_at) VALUES ('2026-07-08','pass','risk_off',"
                     "'2026-11-05T00:00:00')")
    m = _metric(_run(_plan(splits=("regime",))))
    assert set(m.splits["regime"]) == {"risk_off"}


def test_single_day_window_keeps_rows_that_carry_a_time_part(db):
    """P2-H2-1 的第三处：取数窗口也得按日期比。

    `analysis_date BETWEEN '2026-07-08' AND '2026-07-08'` 是字符串比较，会把
    "2026-07-08 09:30" 挡在单日窗口外——样本无声变少，而 notes 里的股票数用的
    是同一条 BETWEEN，两边一起错就没人能发现。
    """
    from src.research.experiment import _sole_benchmark
    _insert([("A", "2026-07-08 09:30", 30.0, 0.03, 0.05)])
    outcome = _run(_plan(splits=()))
    assert _metric(outcome).sample_size == 1
    assert "分析期出现 1 只股票" in outcome.notes
    assert _sole_benchmark("2026-07-08", "2026-07-08") == "sh000300", \
        "基准兜底（U5 的入口）同样按日期取窗口，不能比整串"


def test_notes_say_the_two_denominators_are_not_the_same_set(db):
    """P2-H2-2：旧文案"样本 X 只 / 全市场 Y 只"会被读成覆盖率；分母语义要写清。"""
    _insert([("A", "2026-07-08", 30.0, 0.03, 0.05)])
    with db_mod.db_conn() as conn:
        conn.execute(
            "INSERT INTO instrument (instrument_id, market_id, symbol, status,"
            " created_at, updated_at) VALUES ('CN:999999','CN','999999','active',"
            "'2026-11-05T00:00:00','2026-11-05T00:00:00')")
    notes = _run(_plan(splits=("regime",))).notes
    assert "分析期出现 1 只股票" in notes
    assert "instrument 表当前 1 只" in notes
    assert "不是同一集合" in notes
    assert "不能用 1/1 当覆盖率" in notes


# ── P2-H2-1：holdout 按日期比较 ──────────────────────────

def test_holdout_cutoff_compares_dates_not_strings(db):
    """带时间部分的 analysis_date 在字符串比较下会被分到 holdout 侧。

    cutoff = end − 5 天 = 2026-07-05；旧写法 `"2026-07-05 09:30" > "2026-07-05"`
    为真 → 这行被判进 holdout，而按日期它正好是 cutoff 当天、属于估计集。
    估计集与 holdout 的分界决定预注册算不算数，所以这不是格式洁癖。
    """
    _insert([("A", "2026-07-05 09:30", 30.0, 0.03, 0.05),
             ("B", "2026-07-09", 30.0, 0.09, 0.05)])
    outcome = _run(_plan(splits=(), holdout_days=5,
                         start="2026-07-01", end="2026-07-10"))
    est = _metric(outcome)
    hold = _metric(outcome, "holdout_excess_20_20~40")
    assert est.sample_size == 1      # 07-05 留在估计集
    assert est.value == pytest.approx(0.03)
    assert hold.sample_size == 1     # 只有 07-09 进 holdout
    assert hold.value == pytest.approx(0.09)


def test_unparsable_date_stays_in_estimation_and_is_disclosed(db):
    """日期解析不了的行不许静默消失：留在估计集，并在 notes 里计数。"""
    _insert([("A", "2026-07-1", 30.0, 0.03, 0.05)])   # 在字符串区间内但不是合法日期
    outcome = _run(_plan(splits=(), holdout_days=5,
                         start="2026-07-01", end="2026-07-10"))
    assert {r.metric for r in outcome.results} == {"excess_20_20~40"}
    assert _metric(outcome).sample_size == 1
    assert "1 行日期不可解析" in outcome.notes
