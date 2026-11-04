"""M3 接管变异检查：把每条修复逐条退回"切换日现网的那个形状"，确认单测真的会响。

审计（`docs/worker/audit-20261030-m3-cutover.md`）里的每条 P0/P1 都是一次
"看起来在跑、其实没跑对"的静默故障，所以这里的变异不是改数字，而是**退回旧行为**：
每条都对应现网 2026-10-30 真实发生过的那一幕。
"""
import pathlib
import subprocess
import sys

TARGETS = ["tests/test_m3_takeover.py", "tests/test_m3_verify_t46.py",
           "tests/test_m3_verify_t54.py", "tests/test_minute_accumulate.py",
           "tests/test_minute_source.py", "tests/test_minute_pk_migration.py"]
FILES = {
    "runner": pathlib.Path("paper_trading/integration/rq_engine/runner.py"),
    "queue": pathlib.Path("paper_trading/integration/rq_engine/queue.py"),
    "minute": pathlib.Path("paper_trading/data/minute.py"),
    "loop": pathlib.Path("paper_trading/agent/loop.py"),
    "heartbeat": pathlib.Path("paper_trading/utils/heartbeat.py"),
    "verify": pathlib.Path("paper_trading/utils/m3_verify.py"),
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
    print("%-46s → %-52s %s" % (label, tail[-1] if tail else r.stdout[-160:], verdict))
    return r.returncode != 0


CASES = [
    ("① 决策时点门退回「永远 go」（16:49 决定成交在 09:31）", "runner",
     '        return "wait" if now < decided else "go"',
     '        return "go"'),
    ("② 归因只比标的、不比方向与量（3 executed 对 1 成交）", "queue",
     '''        hit = next((i for i, p in enumerate(pool) if (
            _code6(p.get("symbol")), str(p.get("side") or "BUY").upper(),
            int(p.get("volume") or 0)) == want), None)''',
     '''        hit = next((i for i, p in enumerate(pool) if (
            _code6(p.get("symbol")),) == (want[0],)), None)'''),
    ("③ 落账失败照常把意图标掉（半本账 + 蒸发意图）", "queue",
     "    if not posting_ok:",
     "    if False:"),
    ("④ 提交未成交被静默吞（不 rejected、永远排队）", "queue",
     "        if bid in submitted:",
     "        if False:"),
    ("⑤ 跨日未决意图不过期（昨天的决定交易今天的价格）", "queue",
     '''            "SELECT biz_id FROM rq_intents WHERE status IN ('pending','queued')"
            " AND substr(created_at,1,10) < ?", (day,)).fetchall()''',
     '''            "SELECT biz_id FROM rq_intents WHERE status IN ('pending','queued')"
            " AND 0", (day,)).fetchall()'''),
    ("⑥ 未决去重退回 LIKE（`_` 是通配 + key@ 永不命中 ⇒ 二道防线死）", "queue",
     "        if any(_same_key(r[0], key) and r[1] in (\"pending\", \"queued\") for r in rows):",
     "        if any(r[0] == key and r[1] in (\"pending\", \"queued\") for r in rows):"),
    ("⑦ 盘中采集把「还在走」的那根 bar 也落库（半个 K 线定价）", "minute",
     '''        keep = [r for r in rows
                if str(r.get("ts", ""))[:10] == day
                and str(r.get("ts", ""))[:16] in {f"{day}T{s}" for s in want_set}]''',
     '''        keep = [r for r in rows if str(r.get("ts", ""))[:10] == day]'''),
    ("⑧ 已齐的标的每跳仍重复打接口（盘中采集会打爆行情源）", "minute",
     "        if not missing:",
     "        if False:"),
    ("⑨ AI 直接动作退回旧撮合（新线跑着、旧线也下单=双活）", "loop",
     '                    if ex == "rqalpha":',
     "                    if False:"),
    ("⑩ rq_tick 心跳退回默认表（没切换的部署天天假红）", "heartbeat",
     'DEFAULT_THRESHOLDS = {"engine": 300, "agent": 64800}',
     'DEFAULT_THRESHOLDS = {"engine": 300, "agent": 64800, "rq_tick": 900}'),
    ("⑪ 分钟线供给项退回「不查」（日内停摆没人知道）", "verify",
     '    if executor != "rqalpha":\n        return item("minute_feed", "分钟线供给（新线命脉）", "skip",\n                    "executor 非 rqalpha，本项不适用")',
     '    if executor == "never":\n        return item("minute_feed", "分钟线供给（新线命脉）", "skip", "不适用")'),
    # 下面两条是 2026-10-30 现网验收（scratch 干净账本跑真会话）照出来的形状：
    # 不在纯函数单测里就会被漏掉，所以既补了假 rqalpha 的会话测试，也在这里各挂一条变异。
    ("⑫ 同跳同款去重被当成「已成交」（3 executed 对 1 笔成交）", "queue",
     '        if status == "SKIPPED_DUPLICATE":',
     '        if status == "__NEVER__":'),
    ("⑬ 一条意图在多根 bar 上反复定局（后记的 SKIPPED 洗掉真状态）", "runner",
     "            if bid and bid in self._handled:\n                continue",
     "            if False:\n                continue"),
]

if __name__ == "__main__":
    print("基线（未变异）：")
    base = run("  全绿应为 passed", expect_fail=False)
    if base:
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
