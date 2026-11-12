"""在合成库上跑研究 OS 全链：假设 -> 锁 -> 快照 -> 计划 -> 运行 -> 结论。

用法（在仓库根）:
    STOCK_DASHBOARD_DB=data/db/stock_dashboard_synthetic.db python scripts/verify_synthetic_env.py
"""
import json
import os
import subprocess
import sys

DB = os.environ.get("STOCK_DASHBOARD_DB", "data/db/stock_dashboard_synthetic.db")
PY = sys.executable


def run(args, label):
    print(f"\n===== {label} =====")
    env = dict(os.environ, STOCK_DASHBOARD_DB=DB)
    r = subprocess.run([PY] + args, capture_output=True, text=True, env=env)
    print(r.stdout.strip()[-1200:])
    if r.returncode != 0:
        print("STDERR:", r.stderr.strip()[-800:])
        sys.exit(f"FAILED at {label}")
    return r.stdout.strip()


run(["-m", "src.research.hypothesis", "create",
     "--id", "H-SYN",
     "--statement", "合成环境验证：MOS 越高，60 日超额越高（bake 斜率 0.30）",
     "--prior", "合成 ground truth：excess_60 = 5.0 + 0.30*mos + N(0,6)",
     "--universe", "A",
     "--signal", '{"feature": "mos_at_entry"}',
     "--horizon-days", "60", "--benchmark", "sh000300",
     "--metric", "net", "--threshold", ">0",
     "--regimes", "all", "--costs", "{}",
     "--kill-criteria", "净增量 CI 覆盖 0,任一档 <10 只,样本 <30 只"],
    "create hypothesis H-SYN")
run(["-m", "src.research.hypothesis", "lock", "H-SYN"], "lock H-SYN")

out = run(["-c",
           "from src.research.snapshot import create_snapshot; "
           "print(create_snapshot(['analysis_outcome','financial_history','benchmark_daily'], "
           "snapshot_id='DS-SYN', notes='合成环境验证').snapshot_id)"],
          "create snapshot DS-SYN")
ds_id = out.strip().splitlines()[-1]
print("snapshot_id:", ds_id)

run(["-m", "src.research.experiment", "create",
     "--id", "E-SYN", "--hypothesis", "H-SYN", "--snapshot", ds_id,
     "--dataset", "analysis_outcome", "--universe", "A", "--benchmark", "sh000300",
     "--metric", "net_60_>=40_vs_<=0_risk_off", "--threshold", ">0",
     "--horizon-days", "60", "--regimes", "all", "--costs", "{}",
     "--params", json.dumps({
         "start_date": "2025-07-01", "end_date": "2026-08-31",
         "tiers": [["<=0", None, 0.0], ["0~20", 0.0, 20.0],
                   ["20~40", 20.0, 40.0], [">=40", 40.0, None]],
         "horizons": [60], "n_boot": 1000, "splits": ["regime"],
         "holdout_days": 0, "net": [">=40", "<=0"], "net_regime": "risk_off"})],
    "create plan E-SYN")

run(["-m", "src.research.experiment", "run", "E-SYN"], "run E-SYN")
print("\n===== 验证完成 =====")
