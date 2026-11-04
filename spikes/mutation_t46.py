"""T46 变异检查：把每个检查改回"bug 版"，确认新用例真能杀掉（改完即还原）。"""
import pathlib
import subprocess
import sys

P = pathlib.Path("paper_trading/utils/m3_verify.py")
ORIG = P.read_text(encoding="utf-8")
TARGETS = ["tests/test_m3_verify_t46.py"]

MUT = [
    ("①印花税不分买卖方向",
     'want_stamp = amount * float(cfg.stamp_duty_rate) if int(f["direction"]) == -1 else 0.0',
     'want_stamp = amount * float(cfg.stamp_duty_rate)'),
    ("②NAV 当天 0 行也判绿",
     'if len(todays) == 0:\n        return item("nav", "NAV 收盘记录", "red", f"{today} 没有 NAV 行（面板会断供）",\n                    money=True, on_red="回退")',
     'if len(todays) == 0:\n        return item("nav", "NAV 收盘记录", "green", "当天没有 NAV 行（不判）",\n                    money=True, on_red="回退")'),
    ("③双活阈值放宽到 >2 行",
     "GROUP BY symbol, direction, price, volume, m HAVING n>1",
     "GROUP BY symbol, direction, price, volume, m HAVING n>2"),
    ("④非钱类红也触发回退",
     '"rollback_required": bool(money_reds),',
     '"rollback_required": bool(reds),'),
    ("⑤T+1 去掉当日建仓判据",
     'if entry == today and avail != 0:',
     'if False:'),
    ("⑥无成交时费用检查报绿",
     'return item("fees", "成交费用三件套重算", "warn", "当天没有成交可验（0 笔）",',
     'return item("fees", "成交费用三件套重算", "green", "当天没有成交可验（0 笔）",'),
]

py = sys.executable
for name, old, new in MUT:
    assert old in ORIG, name
    P.write_text(ORIG.replace(old, new, 1), encoding="utf-8")
    r = subprocess.run([py, "-m", "pytest", "-q", "--no-header", *TARGETS],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
    print("%-28s → %s" % (name, tail[-1] if tail else r.stdout[-200:]))
P.write_text(ORIG, encoding="utf-8")
r = subprocess.run([py, "-m", "pytest", "-q", "--no-header", *TARGETS],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print("还原后 →", [l for l in r.stdout.splitlines() if "passed" in l][-1])
