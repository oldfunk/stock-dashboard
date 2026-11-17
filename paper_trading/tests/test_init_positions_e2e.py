"""期初持仓四场景 e2e（T73）：账本 → 框架账户 → 撮合结果，端到端锁死。

为什么必须是 e2e 而不是单测：11-05 那六个 P0 修复每一个都在**局部**测过了
（get_bar 聚合、date→datetime、现金基线……），但 `parse_init_positions` 只认字符串
这一环在配置归一层（rqalpha 内部），任何"我们的函数返回对不对"的测试都看不见它。
四场景各自钉住一条钱类红线（A T+1 冻结拒卖 / B 超支拒买 / C 正常卖成交 /
D 正常买成交），**判别力来自"账本与框架必须一致"**：A/C 都要求框架真的看得见仓
（快照 quantity>0），否则"卖出被拒"是空仓假绿，不是 T+1 生效。

跑法（两种，同一份代码）：
* 有 rqalpha 的环境里 pytest 直接跑（引擎 venv + pytest 时）；
* 现网/离线机：`python tests/test_init_positions_e2e.py --data-db … --bundle …
  --workdir … --python <引擎 venv 的 python>`——父进程只用产品代码造账本副本、
  断言结果，会话一律**子进程**跑（G-T13-9：rqalpha 一天一进程）。

只用**账本副本**（临时目录里现造）与 data.db 只读查询：`ledger=False`，
不写 `paper_account.db`、不写 `op_log`、不碰 `var/rq_state`。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASH = 50_000.0
#: 账本形状 {6 位码: (total_volume, available_volume)}——三个场景三种状态
LEDGER_POSITIONS = {
    "002192": (100, 100),      # 可卖：C 场景要成交
    "300390": (200, 0),        # 全冻：A 场景必须被 T+1 挡下
}
SELLABLE, FROZEN = "002192", "300390"
BUY_TARGET = "000858"          # 账本里没有它，买卖都要现买：B 超支 / D 正常

SCENES = {
    "A_t1_frozen_sell_rejected": {"biz": "t73-a", "symbol": FROZEN,
                                  "action": "sell", "volume": 200},
    "B_overspend_buy_rejected": {"biz": "t73-b", "symbol": BUY_TARGET,
                                 "action": "buy", "volume": 700},
    "C_normal_sell_fills": {"biz": "t73-c", "symbol": SELLABLE,
                            "action": "sell", "volume": 100},
    "D_normal_buy_fills": {"biz": "t73-d", "symbol": BUY_TARGET,
                           "action": "buy", "volume": 100},
}
#: 场景名以 A/B/C/D 开头（判据分支与 `spikes` 里的口语称呼都用前缀）
BY_PREFIX = {k[0]: k for k in SCENES}


def make_ledger(path, *, cash=CASH, positions=None) -> str:
    """造一本**账本副本**（形状来自现网：account/positions 两张钱类表）。

    用 `PaperBroker` 建表——schema 变了这里就先炸，测试不会假装通过。
    """
    from paper_trading.broker.paper_broker import PaperBroker
    from paper_trading.models import TradingConfig

    broker = PaperBroker(db_path=str(path), config=TradingConfig(initial_cash=cash))
    with broker._connect() as conn:                       # noqa: SLF001 - 造副本形状
        conn.execute("UPDATE account SET cash = ?, initial_cash = ? WHERE id = 1",
                     (float(cash), float(cash)))
        conn.execute("DELETE FROM positions")
        for sym, (tot, avail) in sorted((positions or LEDGER_POSITIONS).items()):
            conn.execute(
                "INSERT INTO positions (symbol, total_volume, available_volume,"
                " avg_cost, last_update) VALUES (?, ?, ?, ?, ?)",
                (sym, int(tot), int(avail), 10.0, "2026-01-01T00:00:00"))
    return str(path)


def bars_today(data_db, symbol, today) -> int:
    """data.db 里该标的今天的 5 分钟 bar 根数（会话能否起跑的前置条件）。"""
    with sqlite3.connect(f"file:{data_db}?mode=ro", uri=True) as c:
        return int(c.execute(
            "SELECT COUNT(*) FROM minute_bars WHERE symbol=? AND period='5'"
            " AND adjust='raw' AND ts LIKE ?",
            (symbol, f"{today}%")).fetchone()[0])


# ------------------------------------------------------------------ 子进程：跑会话
def run_child(args) -> int:
    """一个场景一个进程（G-T13-9）：读账本副本 → run_session → 打 JSON。"""
    from paper_trading.integration.rq_engine import runner

    st0 = runner.ledger_init_state(args.ledger)
    spec = SCENES[args.scene]
    plan = [{"biz_id": spec["biz"], "symbol": spec["symbol"],
             "action": spec["action"], "volume": spec["volume"]}]
    import yaml
    trading = (yaml.safe_load(open(args.config, encoding="utf-8")) or {}).get("trading") or {}
    fee = {k: float(trading[k]) for k in
           ("commission_rate", "commission_min", "stamp_duty_rate", "transfer_fee_rate")}
    res = runner.run_session(
        today=args.today, data_db=args.data_db, bundle=args.bundle,
        symbols=[spec["symbol"]], plan=plan, state_dir=args.state,
        workdir=args.state, frequency="1m", ledger=False, fee=fee,
        intraday_resume=True, init_positions=st0["positions"],
        init_cash=st0["cash"], init_frozen=st0["frozen"])
    print(json.dumps({k: res.get(k) for k in
                      ("error", "evidence", "snapshots", "orders", "gates")},
                     ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ 父进程：断言
def _pos(snap, ob) -> dict:
    return (snap or {}).get("positions", {}).get(ob) or {}


def check_scene(name: str, res: dict) -> list[str]:
    """一个场景的判据。返回**不成立**的条目（空列表=绿）。

    每条都带"为什么"：四场景的意义是让读者一眼看出它挡的是哪个 bug，
    而不是攒一堆 `assert True`。
    """
    bad = []
    if res.get("error"):
        return [f"{name}: 会话报错 {res['error']}"]
    ev = res.get("evidence") or {}
    snaps = res.get("snapshots") or []
    snap = next((s for s in snaps if s.get("tag") == "before_trading"), {})
    trades = ev.get("trades") or []
    rejects = ev.get("rejects") or []
    align = ev.get("init_align") or {}
    fz = ev.get("frozen_align") or {}
    spec = SCENES[BY_PREFIX[name[0]]]
    ob = f"{spec['symbol']}.{'XSHG' if spec['symbol'][0] in '69' else 'XSHE'}"
    filled = [t for t in trades if t.get("symbol") == ob]

    # 每一场都得先过这道：**账本持仓确实进了框架**（11-05 P0 的本体）
    if align.get("dropped_silently"):
        bad.append(f"{name}: 期初持仓被框架吞掉 {align['dropped_silently']}")
    if (spec["symbol"] in (SELLABLE, FROZEN)
            and int(_pos(snap, ob).get("quantity", 0)) == 0):
        bad.append(f"{name}: 框架快照里 {ob} 仓位为 0（账本有仓，导入没生效）")

    if name.startswith("A_"):
        # T+1：框架看得见这只仓（quantity>0，不是空仓假绿），但平不了（closable=0）
        frz = next((a for a in (fz.get("applied") or []) if a.get("symbol") == ob), None)
        if not frz:
            bad.append(f"{name}: 账本冻结没落进框架（frozen_align.applied="
                       f"{fz.get('applied')}）")
        elif int((fz.get("closable") or {}).get(ob, -1)) != 0:
            bad.append(f"{name}: 冻结后框架仍可平 {ob}"
                       f"（closable={fz.get('closable')}）")
        if filled:
            bad.append(f"{name}: 冻结仓被 T+0 卖出 {filled}")
    elif name.startswith("B_"):
        if abs(float(snap.get("cash", 0)) - CASH) > 0.01:
            bad.append(f"{name}: 框架现金基线 {snap.get('cash')} ≠ 账本 {CASH}"
                       f"（超支风险：按框架默认 10 万放行买单）")
        if filled:
            bad.append(f"{name}: 超出账本现金的买单被放行 {filled}")
    elif name.startswith("C_"):
        if not filled:
            bad.append(f"{name}: 可卖仓没成交（rejects={rejects[:2]}）")
    elif name.startswith("D_"):
        if not filled:
            bad.append(f"{name}: 账本现金买得起的买单没成交（rejects={rejects[:2]}）")
    if not (ev.get("event_counts") or {}).get("BAR"):
        bad.append(f"{name}: 一根 bar 都没走到（会话空转）")
    return bad


def _spawn(py, name, *, ledger, state, data_db, bundle, config, today) -> dict:
    """一个场景一个子进程，返回 run_session 的关键结果（失败也返回结构化 error）。"""
    cmd = [py, str(Path(__file__).resolve()), "--child", "--scene", name,
           "--ledger", ledger, "--data-db", str(data_db), "--bundle", str(bundle),
           "--state", str(state), "--today", today, "--config", str(config)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    line = [l for l in (proc.stdout or "").splitlines() if l.startswith("{")]
    if proc.returncode != 0 or not line:
        return {"error": f"子进程 rc={proc.returncode} {(proc.stderr or '')[-400:]}"}
    return json.loads(line[-1])


def guard_stale_state(*, data_db, bundle, workdir, python_bin=None, today=None,
                      config=None) -> list[str]:
    """同一状态目录跑两遍 C：框架已卖光、账本副本没动 ⇒ 第二遍必须**拒开**。

    这条不是第四个场景，是修好之后新加的那道闸的可执行证明——现网明天早上就会
    撞上它的同类形状（`var/rq_state/live` 里躺着 11-05 建的空仓账户，而账本有 7 只）。
    拒跑 = 让运维去 `ledger reset-state`，而不是让引擎拿一本与账本脱钩的账户继续交易。
    """
    today = today or date.today().isoformat()
    workdir = Path(workdir)
    ledger = make_ledger(workdir / "guard_ledger.db", cash=CASH)
    state = workdir / "state_guard"
    name = "C_normal_sell_fills"
    args = dict(ledger=ledger, state=state, data_db=data_db, bundle=bundle,
                config=str(config or ROOT / "config.yaml"), today=today)
    first = _spawn(python_bin or sys.executable, name, **args)
    bad = check_scene(name, first)
    if bad:
        return [f"guard/第一跳: {b}" for b in bad]
    second = _spawn(python_bin or sys.executable, name, **args)
    err = str(second.get("error") or "")
    if not err:
        return ["guard/第二跳: 账本与框架已脱钩却仍开会话（少仓校验没生效）"]
    if "框架账户比账本少" not in err:
        return [f"guard/第二跳: 拒跑原因不是少仓校验：{err[:200]}"]
    return []


def run_scenes(*, data_db, bundle, workdir, python_bin=None, today=None,
               config=None) -> dict:
    """四场景各起一个子进程，返回 `{scene: {"failures": [...], "res": {...}}}`。"""
    today = today or date.today().isoformat()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    ledger = make_ledger(workdir / "ledger_copy.db", cash=CASH)
    py = python_bin or sys.executable
    out = {}
    for name in SCENES:
        res = _spawn(py, name, ledger=ledger, state=workdir / f"state_{name}",
                     data_db=data_db, bundle=bundle,
                     config=str(config or ROOT / "config.yaml"), today=today)
        out[name] = {"failures": check_scene(name, res), "res": res}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", action="store_true", help="子进程模式（内部用）")
    ap.add_argument("--scene")
    ap.add_argument("--ledger")
    ap.add_argument("--state")
    ap.add_argument("--data-db")
    ap.add_argument("--bundle")
    ap.add_argument("--config")
    ap.add_argument("--today")
    ap.add_argument("--python", dest="python_bin")
    ap.add_argument("--workdir")
    ap.add_argument("--out", help="把结果 JSON 写到这里（stdout 会被日志污染）")
    ap.add_argument("--no-guard", action="store_true",
                    help="只跑四场景，跳过『账本↔框架脱钩拒跑』那条额外证明")
    a = ap.parse_args()
    if a.child:
        return run_child(a)
    today = a.today or date.today().isoformat()
    ctx = (tempfile.TemporaryDirectory(prefix="t73-scenes-") if not a.workdir
           else contextlib.nullcontext(Path(a.workdir)))
    with ctx as wd:
        if a.workdir:
            Path(wd).mkdir(parents=True, exist_ok=True)
        need = [SELLABLE, FROZEN, BUY_TARGET]
        missing = {s: bars_today(a.data_db, s, today) for s in need
                   if bars_today(a.data_db, s, today) < 48}
        if missing:
            print(json.dumps({"skipped": "当日分钟线不足，无法跑四场景",
                              "bars": missing}, ensure_ascii=False))
            return 0
        results = run_scenes(data_db=a.data_db, bundle=a.bundle, workdir=wd,
                             python_bin=a.python_bin, today=today, config=a.config)
        guard = ([] if a.no_guard else
                 guard_stale_state(data_db=a.data_db, bundle=a.bundle, workdir=wd,
                                   python_bin=a.python_bin, today=today,
                                   config=a.config))
    fails = [f for r in results.values() for f in r["failures"]] + guard
    payload = json.dumps({
        "today": today,
        "guard_stale_state": "GREEN" if not guard else guard,
        "scenes": {k: ("GREEN" if not v["failures"] else v["failures"])
                   for k, v in results.items()},
        "framework_positions": {
            k: (v.get("res", {}).get("snapshots") or [{}])[0].get("positions")
            for k, v in results.items()},
        "rejects": {k: (v.get("res", {}).get("evidence") or {}).get("rejects")
                    for k, v in results.items()},
        "frozen_align": {k: (v.get("res", {}).get("evidence") or {}).get("frozen_align")
                         for k, v in results.items()},
    }, ensure_ascii=False, indent=1)
    # PaperBroker 的 INFO 日志走 stdout，混进 JSON 里就没法机读——要落文件就 --out
    if a.out:
        Path(a.out).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 1 if fails else 0


def test_init_positions_four_scenes():
    """pytest 入口：环境不齐就跳过（CI 的产品 venv 没有 rqalpha）。"""
    import importlib.util
    import os

    if importlib.util.find_spec("rqalpha") is None:
        _pytest_skip("本解释器没有 rqalpha（引擎 venv 才有）")
    data_db = os.environ.get("PAPER_TRADING_DATA_DB") or str(ROOT / "data.db")
    bundle = os.environ.get("PAPER_TRADING_RQ_BUNDLE") or str(
        Path.home() / ".rqalpha" / "bundle")
    py = os.environ.get("PAPER_TRADING_ENGINE_PYTHON") or sys.executable
    today = date.today().isoformat()
    if not Path(data_db).exists() or not Path(bundle).exists():
        _pytest_skip(f"缺 data.db({data_db}) 或 bundle({bundle})")
    if bars_today(data_db, SELLABLE, today) < 48:
        _pytest_skip(f"{today} 没有分钟线（非交易日或库里没当日数据）")
    with tempfile.TemporaryDirectory(prefix="t73-") as wd:
        results = run_scenes(data_db=data_db, bundle=bundle, workdir=wd,
                             python_bin=py, today=today)
        guard = guard_stale_state(data_db=data_db, bundle=bundle, workdir=wd,
                                  python_bin=py, today=today)
    fails = [f"{k}: {x}" for k, v in results.items() for x in v["failures"]] + guard
    assert not fails, "四场景+脱钩闸未全绿：\n" + "\n".join(fails)


def _pytest_skip(why: str):
    import pytest

    pytest.skip(why, allow_module_level=False)


if __name__ == "__main__":
    sys.exit(main())
