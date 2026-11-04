"""T53 日期卫生：新鲜度断言必须与墙钟解耦（基准日可注入），fixture 不许留近今天的写死日期。

两次 flaky 教训钉在这里：
- ① `test_mother_contract` 的"最新一日"写死成 `2026-10-26`，而 `mother.daily_bars` 的
  stale 判定读墙钟 ⇒ 真实时间推进、lag 跨过 3 天窗口就翻红（10-29 绿、10-30 红，实测）。
- ② `today+10` 的偏移正好落在周六 ⇒ 交易日语义的断言随机失效（T34 修过一次）。

**为什么不是 monkeypatch 假时钟**：`sources.block()` 里是**函数内**
`from datetime import date`（`sources.py:36`），模块属性替换拦不住；而且 fixture 用
真时钟算、产品用假时钟读，测出来的"红"是仪器误差不是缺陷。
⇒ 根治是把基准日变成参数（与 T41 `sync_raw(today=)`、T50 `get_quotes(now=)` 同一约定），
测试自己把 `today` 交给被测函数。
"""
import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from paper_trading.integration import mother                     # noqa: E402
from paper_trading.integration.sources import _is_stale, block   # noqa: E402

NEAR_WINDOW_DAYS = 45
#: 只有"故意是历史"的场景允许写死日期（分红报告期 / 无重叠对照）
HISTORIC_OK = {"2025-12-31", "2026-09-01"}


def _shift(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- ①基准日可注入
def test_block_stale_is_a_function_of_the_given_today():
    """同一 asof、同一窗口：基准日不同 ⇒ 结论不同，且完全不读墙钟。"""
    asof = "2026-05-08"
    assert block({"x": 1}, "mother:t", asof=asof, max_age_days=3,
                 today="2026-05-10")["stale"] is False
    assert block({"x": 1}, "mother:t", asof=asof, max_age_days=3,
                 today="2026-05-12")["stale"] is True
    assert _is_stale("2020-01-01", 3, date.today().isoformat()) is True


def test_daily_bars_accepts_explicit_today(tmp_path):
    """`daily_bars(today=)` 注入后，"母库新鲜"这条断言与日历再无关系。"""
    import test_mother_contract as mc

    sd = mc._mk_sd(tmp_path)
    mother.reset_cache()
    try:
        fresh = mother.daily_bars(["600519"], days=5, local_bars=mc._local_bar,
                                  mother=sd, today=mc.D_LATEST)
        assert fresh["600519"]["asof"] == mc.D_LATEST
        assert fresh["600519"]["stale"] is False
        stale = mother.daily_bars(["600519"], days=5, local_bars=mc._local_bar,
                                  mother=sd, today=_shift(mc.D_LATEST, 4))
        assert stale["600519"]["stale"] is True, "跨过 3 自然日窗口必须判过期"
    finally:
        mother.reset_cache()


def test_default_today_is_unchanged(tmp_path):
    """不传 today ⇒ 语义与"显式传真实今天"逐位一致（默认路径不被注入点改变）。

    比对时剔掉 `stale`：那个字段**定义上**是"基准日的函数"，
    `spikes/clock_shift_t53.py` 拨快产品时钟时它必然不同——那是仪器读数差，
    不是回归（真实日历推进时两边同钟）。
    """
    import test_mother_contract as mc

    sd = mc._mk_sd(tmp_path)
    mother.reset_cache()
    try:
        a = mother.daily_bars(["600519"], days=5, local_bars=mc._local_bar, mother=sd)
        b = mother.daily_bars(["600519"], days=5, local_bars=mc._local_bar,
                              mother=sd, today=date.today().isoformat())
        assert {k: v for k, v in a["600519"].items() if k != "stale"} == \
               {k: v for k, v in b["600519"].items() if k != "stale"}
        assert "stale" in a["600519"] and "stale" in b["600519"]
    finally:
        mother.reset_cache()


# ---------------------------------------------------------------- ②fixture 规则
def test_relative_dates_are_never_weekends():
    """②的教训：由偏移算出的"最近工作日"不许落在周六/周日。"""
    import test_mother_contract as mc

    named = {"D_LATEST": mc.D_LATEST, "D_PREV": mc.D_PREV, "D_NEXT": mc.D_NEXT}
    named.update({f"D_SEQ[{i}]": d for i, d in enumerate(mc.D_SEQ)})
    for label, value in named.items():
        d = date.fromisoformat(value)
        assert d.weekday() < 5, f"{label}={value} 是 {d.strftime('%a')}（周末）"
    assert mc.D_SEQ == sorted(mc.D_SEQ), "本地三日必须升序"
    lag = (date.today() - date.fromisoformat(mc.D_LATEST)).days
    assert 0 <= lag <= 3, f"D_LATEST 距今天 {lag} 天，超出 3 自然日新鲜窗口"


def test_fixture_dates_follow_the_clock():
    """fixture 的基准日必须与"产品读到的今天"同源（差得远就说明又写死了）。"""
    import test_mother_contract as mc

    for label, value in (("D_PREV", mc.D_PREV), ("D_SEQ[-1]", mc.D_SEQ[-1])):
        lag = (date.today() - date.fromisoformat(value)).days
        assert 0 <= lag <= 5, f"{label}={value} 距今天 {lag} 天 ⇒ 已经老化"


# ---------------------------------------------------------------- ③扫描守卫
def _load_scanner():
    path = ROOT / "spikes" / "date_flaky_scan_t53.py"
    spec = importlib.util.spec_from_file_location("date_flaky_scan_t53", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cured_file_has_no_near_today_literals():
    """`test_mother_contract.py` 里不许再有"距今天 <45 天"的绝对日期（历史场景除外）。"""
    scanner = _load_scanner()
    text = (ROOT / "tests" / "test_mother_contract.py").read_text(encoding="utf-8")
    today = date.today()
    near = [raw for raw, _line, d in scanner.ast_dates(text, is_text=True)
            if abs((d - today).days) < NEAR_WINDOW_DAYS and raw not in HISTORIC_OK]
    assert near == [], f"又写死了近今天的日期：{sorted(set(near))}"


def test_scanner_bites_but_ignores_history(tmp_path):
    """正控制：植入近今天的日期必须被抓到；故意写死的历史日期不算风险。"""
    scanner = _load_scanner()
    near = (date.today() + timedelta(days=3)).isoformat()
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "from datetime import date\n"
        "HISTORIC = '2025-12-31'\n"
        f"NEAR = '{near}'\n"
        "def test_probe():\n"
        "    assert date.today()\n",
        encoding="utf-8")
    rows = scanner.classify(probe, date.today())
    aging = {a["date"] for a in rows["aging"]}
    assert aging == {near}, f"扫描器抓错：{aging}"
    assert rows["reads_wall_clock"] is True and rows["risks"], rows
