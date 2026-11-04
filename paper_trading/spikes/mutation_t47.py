"""T47 变异检查：把 G1-G4 每条防线逐条改回"没有它"的样子，确认新用例真能杀掉。

和 T41/T46/T50 同一套做法：**改源码而不是改断言**——只有源码级变异才能证明
"这条测试在守的就是这条规矩"，而不是在守我随手写的另一个实现。
每处变异跑完立刻还原（脚本结束再整体还原一次兜底）。
"""
import pathlib
import subprocess
import sys

TARGETS = ["tests/test_ledger_gaps_t47.py", "tests/test_rqalpha_adapter.py",
           "tests/test_engine_switch.py"]
FILES = {
    "broker": pathlib.Path("paper_trading/broker/paper_broker.py"),
    "ledger": pathlib.Path("paper_trading/integration/rq_engine/ledger.py"),
    "config": pathlib.Path("paper_trading/engine/config.py"),
    "runner": pathlib.Path("paper_trading/integration/rq_engine/runner.py"),
}
ORIG = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
py = sys.executable


def patch(key, old, new):
    assert old in ORIG[key], f"锚点丢失：{key}\n{old!r}"
    FILES[key].write_text(ORIG[key].replace(old, new, 1), encoding="utf-8")


def run(label):
    r = subprocess.run([py, "-m", "pytest", "-q", "--no-header", *TARGETS],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l
            or "error" in l]
    print("%-46s → %s" % (label, tail[-1] if tail else r.stdout[-200:]))
    return r


def restore(key):
    FILES[key].write_text(ORIG[key], encoding="utf-8")


# ① G1：现金守卫失效（付不起也写）
patch("broker", "            if need > have + 1e-6:", "            if False:")
run("① G1 apply_external_fill 不查现金")
restore("broker")

# ② G1：卖出可卖量守卫失效（T+1 当天超卖也写）
patch("broker", "            if avail < vol:", "            if False:")
run("② G1 apply_external_fill 不查可卖量")
restore("broker")

# ③ G1：缺费用分量时用 cost 反推（猜出来的拆分）
patch("ledger",
      '    if not all(k in t for k in ("commission", "tax", "other_fees")):\n'
      '        return None\n',
      '    if not all(k in t for k in ("commission", "tax", "other_fees")):\n'
      '        c = float(t.get("cost") or 0.0)\n'
      '        return {"commission": c, "stamp_duty": 0.0, "transfer_fee": 0.0}\n')
run("③ G1 费用拆分缺失就猜")
restore("ledger")

# ④ G1：幂等判定失效（biz_id 查不到）
patch("ledger", "    return broker.has_op_with(action, biz_id)", "    return False")
run("④ G1 biz_id 幂等判定失效")
restore("ledger")

# ⑤ G1：守卫失败不挺批次（继续往后写）
patch("ledger", "            break                      # 守卫失败就停，不继续写后面的笔",
      "            continue")
run("⑤ G1 守卫失败后继续写后面的笔")
restore("ledger")

# ⑥ G2：同日判重失效
patch("ledger",
      "    if broker.has_operation(NAV_BIZ_PREFIX, today) or \\\n"
      "            broker.has_operation(\"nav:close\", today):",
      "    if False:")
run("⑥ G2 收盘 NAV 同日判重失效")
restore("ledger")

# ⑦ G2：缺价照记（get_nav 把缺价当 0 市值）
patch("ledger",
      '    missing = [s for s in positions if not prices.get(s)]\n',
      '    missing = []\n')
run("⑦ G2 持仓缺价不拦")
restore("ledger")

# ⑧ G3：executor 不进 DEFAULTS 白名单（写了也被静默丢掉）
patch("config", '    "executor": "legacy",\n', '')
run("⑧ G3 executor 掉出 DEFAULTS")
restore("config")

# ⑨ G3：未知值静默降级 legacy
patch("ledger",
      '    if raw not in ("legacy", "rqalpha"):\n'
      '        raise ValueError(f"engine.executor 只能是 legacy|rqalpha，收到 {raw!r}")\n',
      '    if raw not in ("legacy", "rqalpha"):\n        raw = "legacy"\n')
run("⑨ G3 未知底座静默降级")
restore("ledger")

# ⑩ G3：ledger 不进会话指纹（换语义还能续跑同一状态）
patch("runner", '        "ledger": cfg["mod"]["ptpaper"]["ledger"],\n', '')
run("⑩ G3 ledger 不进 session_fingerprint")
restore("runner")

# ⑪ G4：不拒绝有数据的账本（直接抹掉现网账）
patch("ledger", "    if rows_before and not allow_existing:", "    if False:")
run("⑪ G4 非空账本不拒绝重开")
restore("ledger")

# ⑫ G4：未解冻的 T+1 不搬（切换日买的票永远卖不掉）
patch("ledger", "        for f in frozen:", "        for f in []:")
run("⑫ G4 不搬 t1_freeze")
restore("ledger")

# ⑬ G4：不看 sidecar（和正在写的事务抢账本）
patch("ledger",
      '    for sidecar in (src.with_name(src.name + "-journal"),'
      ' src.with_name(src.name + "-wal")):\n',
      '    for sidecar in ():\n')
run("⑬ G4 不查在写事务")
restore("ledger")

# ⑭ G3：rqalpha 没装也照样起（§5.2"不许静默降级"失效）
patch("ledger", '    if ex == "rqalpha" and not rqalpha_available:', '    if False:')
run("⑭ G3 框架缺失不拦启动")
restore("ledger")

run("还原后（应全绿）")
