import pathlib, subprocess, sys

P = pathlib.Path("paper_trading/data/raw_feed.py")
ORIG = P.read_text(encoding="utf-8")
TARGETS = ["tests/test_raw_pipeline_t41.py", "tests/test_raw_feed.py",
           "tests/test_caliber_defense_t34.py"]

MUT = [
    ("①形状回归：把内层参照表改回外层包",
     "        refs = {s: (raw.get(s) or {}) for s in syms}",
     "        refs = {s: raw for s in syms}"),
    ("②窗口对齐：参照查询丢掉 since（回到 LIMIT days 交易日）",
     "        raw = _m.raw_close_map(syms, since=start.isoformat()) or {}",
     "        raw = _m.raw_close_map(syms, days=days) or {}"),
    ("③单源语义：reference_for 永远返回 None（旗子失效）",
     "    return NO_REFERENCE if no_mother_ref else None",
     "    return None"),
    ("④逐日台账：unflipped 分支不算停线",
     '        elif daily["unflipped"]:\n            reason, stop = "checked_not_flipped", True',
     '        elif False:\n            reason, stop = "checked_not_flipped", True'),
]

py = sys.executable
for name, old, new in MUT:
    assert old in ORIG, name
    P.write_text(ORIG.replace(old, new, 1), encoding="utf-8")
    r = subprocess.run([py, "-m", "pytest", "-q", "--no-header", *TARGETS],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    print("%-46s → %s" % (name, tail[-1] if tail else r.stdout[-160:]))
P.write_text(ORIG, encoding="utf-8")
r = subprocess.run([py, "-m", "pytest", "-q", "--no-header", *TARGETS],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print("还原后 →", [l for l in r.stdout.splitlines() if "passed" in l][-1])
