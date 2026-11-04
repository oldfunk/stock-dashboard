"""T50 变异检查：把 (a)/(c) 改回 bug 版，确认新用例真能杀掉（改完即还原）。"""
import pathlib
import subprocess
import sys

TARGETS = ["tests/test_postclose_defense_t50.py", "tests/test_raw_feed.py",
           "tests/test_caliber_defense_t34.py"]
FILES = {
    "realtime": pathlib.Path("paper_trading/data/realtime.py"),
    "raw_feed": pathlib.Path("paper_trading/data/raw_feed.py"),
    "source": pathlib.Path("paper_trading/data/source.py"),
    "store": pathlib.Path("paper_trading/integration/rq_engine/store.py"),
}
ORIG = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}

py = sys.executable


def patch(key, old, new):
    assert old in ORIG[key], f"锚点丢失：{key}"
    FILES[key].write_text(ORIG[key].replace(old, new, 1), encoding="utf-8")


def run(label):
    r = subprocess.run([py, "-m", "pytest", "-q", "--no-header", *TARGETS],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    print("%-52s → %s" % (label, tail[-1] if tail else r.stdout[-200:]))
    return r


# ① 盘后不放行：quote_usable 忽略 allow_closing
patch("realtime",
      "    if quote_fresh(quote, now):\n        return True\n    return bool(allow_closing) and is_closing_snapshot(quote, now)",
      "    return quote_fresh(quote, now)")
run("① (a) 盘后不放行收盘快照")
FILES["realtime"].write_text(ORIG["realtime"], encoding="utf-8")

# ② intraday_reference 不再带 allow_closing
patch("raw_feed",
      "                return _gq([str(c) for c in codes], allow_closing=True)",
      "                return _gq([str(c) for c in codes])")
run("② intraday_reference 丢掉 allow_closing")
FILES["raw_feed"].write_text(ORIG["raw_feed"], encoding="utf-8")

# ③ 引擎取数口不查
patch("source", "        if require_today_reference:\n", "        if False:\n")
run("③ source.engine_raw_bars 不查当日参照")
FILES["source"].write_text(ORIG["source"], encoding="utf-8")

# ④ store 不查
patch("store",
      '            if require_today_reference and today_d is not None:\n',
      '            if False:\n')
run("④ RawDayBarStore 不查当日参照")
FILES["store"].write_text(ORIG["store"], encoding="utf-8")

run("还原后（应全绿）")
