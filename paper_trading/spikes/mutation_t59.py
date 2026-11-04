"""T59/T60 变异检查：把"交易所判市"和"闸的显示口径"逐条退回 2026-11-03 现网那一版。

这轮的两个缺陷都不是"算错了数字"，而是**同一个事实在五个地方各判一遍**、
以及**报警把全池缺口显示成 5 只**——所以变异全部是"退回旧写法"：

- 段表四处各自判市（920 段判成沪/深）→ 分钟线 0 行、实时报价空、
  `order_book_id` 挂错交易所、涨跌停带按主板 10% 算；
- `raw_gate` 的 `stale[:5]` 截断显示 → 现网据此开出"5 只未翻转"的任务，
  真实情况是全池 38 只都缺 11-02 那根；
- 盘中采集每跳都落 `op_log[minute:gap]` → 一个上午 92 条，真故障被埋掉。
"""
import pathlib
import subprocess
import sys

TARGETS = ["tests/test_m3_takeover.py", "tests/test_market_caliber_t59.py",
           "tests/test_minute_supply_t59.py",
           "tests/test_realtime.py", "tests/test_minute_source.py",
           "tests/test_rqalpha_adapter.py", "tests/test_m3_verify_t46.py",
           "tests/test_m3_verify_t54.py", "tests/test_m3_takeover.py"]
FILES = {
    "markets": pathlib.Path("paper_trading/utils/markets.py"),
    "minute": pathlib.Path("paper_trading/data/minute.py"),
    "realtime": pathlib.Path("paper_trading/data/realtime.py"),
    "store": pathlib.Path("paper_trading/integration/rq_engine/store.py"),
    "bands": pathlib.Path("paper_trading/integration/rq_engine/bands.py"),
    "verify": pathlib.Path("paper_trading/utils/m3_verify.py"),
    "cli": pathlib.Path("paper_trading/cli.py"),
    "runner": pathlib.Path("paper_trading/integration/rq_engine/runner.py"),
}
ORIG = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
py = sys.executable


def patch(key, old, new):
    assert old in ORIG[key], f"锚点丢失：{key}\n{old!r}"
    FILES[key].write_text(ORIG[key].replace(old, new, 1), encoding="utf-8")


def restore_all():
    for k, txt in ORIG.items():
        FILES[k].write_text(txt, encoding="utf-8")


def run(label, expect_fail=True):
    r = subprocess.run([py, "-m", "pytest", "-q", "--no-header", *TARGETS],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [l for l in r.stdout.splitlines()
            if "passed" in l or "failed" in l or "error" in l]
    verdict = ("捕获 ✅" if r.returncode != 0 else "**漏网 ❌**") if expect_fail \
        else ("基线绿 ✅" if r.returncode == 0 else "**基线就不绿 ❌**")
    print("%-46s → %-50s %s" % (label, tail[-1] if tail else r.stdout[-160:], verdict))
    return r.returncode != 0


CASES = [
    ("① 段表退回「首字符判市」（920 段→sh，新浪 0 行）", "markets",
     '''    if code[:2] in BJ_SEGMENTS:
        return BJ
    if code[0] in SH_FIRST:
        return SH''',
     '''    if code[0] in "48":
        return BJ
    if code[0] in SH_FIRST:
        return SH'''),
    ("② 分钟线前缀退回独立判市（首字符 4/8）", "minute",
     "    return markets.exchange_of(symbol)",
     '    return "bj" if str(symbol or "")[:1] in "48" else "sh"'),
    ("③ 实时报价口退回旧规则（920→sz，腾讯回空）", "realtime",
     "    return markets.sina_symbol(code)",
     '    c = str(code).strip()\n'
     '    if c.startswith("6"):\n        return "sh" + c\n'
     '    if c.startswith(("8", "4")):\n        return "bj" + c\n'
     '    return "sz" + c'),
    ("④ rqalpha 标的口退回首字符（920156.XSHG 挂错交易所）", "store",
     "    return markets.order_book_id(symbol)",
     '    code = str(symbol or "").strip().zfill(6)\n'
     '    if code[0] in ("6", "9"):\n        return f"{code}.XSHG"\n'
     '    if code[0] in ("4", "8"):\n        return f"{code}.BJ"\n'
     '    return f"{code}.XSHE"'),
    ("⑤ 涨跌停带退回首字符判北交所（920 段吃 10% 档）", "bands",
     "    if code[:2] in BSE_PREFIXES:",
     '    if code[0] in ("4", "8"):'),
    ("⑥ raw_gate 退回 stale[:5] 截断显示（现网据此误读成 5 只）", "verify",
     '''        return item("raw_gate", "行情口径与新鲜度闸", "red",
                    "；".join(bits) + "——引擎会对这些标的 fail-closed 拒单",
                    on_red="回退（价都不对，账不可能对）")''',
     '''        return item("raw_gate", "行情口径与新鲜度闸", "red",
                    f"stale={stale[:5]} 未翻转={flip['violations'][:5]} "
                    f"unflipped_days={flip['unflipped_days']}",
                    on_red="回退（价都不对，账不可能对）")'''),
    ("⑧ bundle 没这只标的时退回「什么都不做」（挂到明天悄悄过期）", "runner",
     "            if not bid or bid in self._handled or ob in known:",
     "            if True:\n                continue"),
    ("⑨ 把「问不到表」与「表里没有这只」混成一件（空集合不定局）", "runner",
     "        if known_order_book_ids is None:\n            return []",
     "        if not known_order_book_ids:\n            return []"),
    ("⑩ IdleSpin 退回「已解释也照喊」（报警失真）", "runner",
     "               and str(p.get(\"biz_id\") or \"\") not in settled]",
     "               ]"),
    ("⑪ 当日带退回「拿最近可得的收盘凑」（今早真实状态：raw 只到 10-30）", "store",
     "                     require_prev_close: bool = True):",
     "                     require_prev_close: bool = False):"),
    ("⑫ 分钟线陈旧度退回按墙钟算（午休每小时假红一次）", "verify",
     '''        want = latest_expected_ts(today, "5", tn)
        try:
            lag = 0.0 if not want else (
                (datetime.fromisoformat(want)
                 - datetime.fromisoformat(str(latest)[:16])).total_seconds() / 60.0)
        except ValueError:
            lag = (tn - datetime.fromisoformat(str(latest))).total_seconds() / 60.0''',
     '''        want = None
        try:
            lag = (tn - datetime.fromisoformat(str(latest))).total_seconds() / 60.0
        except ValueError:
            lag = 0.0'''),
    ("⑦ 盘中采集退回「每跳都落流水」（一上午 92 条，埋掉真故障）", "cli",
     '                    if res["errors"] and not bridge.broker.has_op_with("minute:gap", fp):',
     '                    if res["errors"]:'),
]

if __name__ == "__main__":
    print("基线（未变异）：")
    if run("  全绿应为 passed", expect_fail=False):
        restore_all()
        raise SystemExit("基线就不绿，先修测试")
    missed = []
    try:
        for label, key, old, new in CASES:
            restore_all()
            patch(key, old, new)
            if not run(label):
                missed.append(label)
    finally:
        restore_all()
    print("\n结论：%d 条变异，%d 条被单测捕获" % (len(CASES), len(CASES) - len(missed)))
    for m in missed:
        print("  漏网：", m)
    sys.exit(1 if missed else 0)
