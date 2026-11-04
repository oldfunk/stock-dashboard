"""T52 方案①改良 spike：日内 state 续跑 + 意图幂等（主 AI，2026-10-30）。

验证的核心问题（T48 评估①的"重复成交"是否被 state 续跑 + 意图幂等化解）：
  进程 1：意图 A(000792 买 100) → 成交 1
  进程 2（同日、同 state、续跑）：意图 A + 新意图 B(600519 买 100)
    → **只应成交 B**（A 已执行——executed.json 模拟账本当日 orders 记录）
  进程 3（同日再续跑）：重复 A+B → **零新增**

改造点（都在本 spike 内，不碰 T13 对照件）：
  ① 闸放宽：T13SimMod._session_gate 的"同日拒绝"改为"同日 + 有 state + 旗开 ⇒ 续跑"
  ② 意图幂等：handle_bar 执行前查 executed.json，已执行 biz_id 跳过

用法：python spikes/intraday_resume_t52.py（自起子进程三连，末行输出 JSON 结论）
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = Path(os.environ.get(
    "T13_FIXTURE",
    str(Path(os.environ["LOCALAPPDATA"]) / "Temp" / "qoder_t13" / "t52_fx.db")))
BUNDLE = r"C:\Users\yanzh\.rqalpha\bundle"


def _run_one(fixture: str, today: str, state: str, executed: str,
             plan: list, evidence: str) -> dict:
    """单次日内会话：patch 闸 + 幂等 handler + run_func（进程内一次）。"""
    sys.path.insert(0, str(HERE))
    import rqalpha_mod_t13sim as m13                # noqa: E402
    import live_sim_t13 as lt13                     # noqa: E402

    # ① 闸放宽：同日 + 有 state + T52_INTRADAY=1 ⇒ 允许续跑（其余语义不变）
    orig_gate = m13.T13SimMod._session_gate

    def gate(self, provider, fingerprint, today_d):
        meta_p = m13.T13SimMod._meta_path(provider)
        if meta_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            if meta.get("fingerprint") != fingerprint:
                raise RuntimeError("GATE: fingerprint mismatch")
            if meta.get("last_day", "") >= today_d.isoformat():
                intraday = (os.environ.get("T52_INTRADAY") == "1"
                            and bool(provider.keys()))
                if not intraday:
                    raise RuntimeError("GATE: same-day rerun refused")
                return                      # 日内续跑放行
    m13.T13SimMod._session_gate = gate
    del orig_gate

    # ② 意图幂等：读"账本当日已执行清单"（真实版=orders 表；spike 用 JSON 等价）
    ex_path = Path(executed)
    done = set(json.loads(ex_path.read_text(encoding="utf-8"))) if ex_path.exists() else set()

    def handle_bar(context, bar_dict):     # noqa: ANN001
        from rqalpha.api import order_shares
        for a in plan:
            bid = a["biz_id"]
            if bid in done:
                continue                    # 已执行——账本里有，跳过
            order_shares(a["symbol"], a["volume"])
            done.add(bid)
        ex_path.write_text(json.dumps(sorted(done)), encoding="utf-8")

    fingerprint = hashlib.sha256(json.dumps({
        "init_cash": lt13.INIT_CASH, "fixture": str(Path(fixture).resolve()),
        "caliber": "raw", "bands": True, "bundle": BUNDLE,
    }, sort_keys=True).encode()).hexdigest()[:16]
    cfg = lt13.build_config(fixture, today, state, evidence, False, True,
                            True, True, "raw", False)
    cfg["mod"]["t13sim"]["fingerprint"] = fingerprint
    from rqalpha import run_func
    try:
        run_func(config=cfg, before_trading=lt13.before_trading,
                 handle_bar=handle_bar, after_trading=lt13.after_trading)
        err = None
    except Exception as e:                  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:200]}"
    ev = {}
    p = Path(evidence)
    if p.exists():
        ev = json.loads(p.read_text(encoding="utf-8"))
    return {"error": err, "trades": ev.get("trades", []),
            "executed": sorted(done)}


def _mkfixture(fixture: str, csv_path: str) -> None:
    """按现 schema（9 列含 adjust）自建 fixture——T13 init-db 与 T31 后 schema 脱节。"""
    import csv
    import sqlite3
    Path(fixture).parent.mkdir(parents=True, exist_ok=True)
    if Path(fixture).exists():
        Path(fixture).unlink()
    c = sqlite3.connect(fixture)
    c.execute("""CREATE TABLE daily_bars (
        symbol TEXT NOT NULL, timestamp TEXT NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
        close REAL NOT NULL, volume INTEGER NOT NULL, turn REAL DEFAULT 0.0,
        adjust TEXT DEFAULT 'qfq', PRIMARY KEY (symbol, timestamp))""")
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [(r[0], r[1], float(r[2]), float(r[3]), float(r[4]),
                 float(r[5]), int(float(r[6])), float(r[7] or 0), "qfq")
                for r in csv.reader(f) if r and r[0] != "symbol"]
    c.executemany("INSERT INTO daily_bars VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    print(f"fixture ready: {len(rows)} rows -> {fixture}")


def main() -> int:
    if not FIXTURE.exists() or FIXTURE.stat().st_size < 10_000:
        _mkfixture(str(FIXTURE), str(HERE.parent / "AppData" / "Local" / "Temp"
                                    / "qoder_t13" / "daily_bars.csv")
                   if False else
                   os.path.expandvars(r"%LOCALAPPDATA%\Temp\qoder_t13\daily_bars.csv"))
    work = Path(tempfile.mkdtemp(prefix="t52_ir_"))
    state, evd = str(work / "state"), str(work / "ev")
    ex = str(work / "executed.json")
    today = "2026-10-27"                    # fixture 内的交易日
    A = {"biz_id": "A-000792", "symbol": "000792.XSHE", "volume": 100}
    B = {"biz_id": "B-000858", "symbol": "000858.XSHE", "volume": 100}

    def fire(tag, plan):
        out = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--one",
             "--fixture", str(FIXTURE), "--today", today, "--state", state,
             "--executed", ex, "--evidence", f"{evd}_{tag}.json",
             "--plan", json.dumps(plan)],
            capture_output=True, text=True, env={**os.environ, "T52_INTRADAY": "1"})
        try:
            return json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:                  # noqa: BLE001
            return {"error": (out.stdout + out.stderr)[-400:]}

    r1 = fire("p1", [A])
    r2 = fire("p2", [A, B])
    r3 = fire("p3", [A, B])

    def n(t):
        return len(t.get("trades") or [])

    res = {
        "p1_trades": n(r1), "p1_err": r1.get("error"),
        "p2_trades": n(r2), "p2_err": r2.get("error"),
        "p2_symbols": [t.get("symbol") or t.get("order_book_id")
                       for t in (r2.get("trades") or [])],
        "p3_trades": n(r3), "p3_err": r3.get("error"),
    }
    ok = (res["p1_trades"] == 1 and res["p2_trades"] == 1
          and res["p2_symbols"] == ["000858.XSHE"] and res["p3_trades"] == 0
          and not res["p1_err"] and not res["p2_err"] and not res["p3_err"])
    res["verdict"] = ("PASS: 日内续跑+意图幂等成立——重复意图零成交、新意图正常成交"
                      if ok else "FAIL")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--one" in sys.argv:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--one", action="store_true")
        ap.add_argument("--fixture", required=True)
        ap.add_argument("--today", required=True)
        ap.add_argument("--state", required=True)
        ap.add_argument("--executed", required=True)
        ap.add_argument("--evidence", required=True)
        ap.add_argument("--plan", required=True)
        a = ap.parse_args()
        print(json.dumps(_run_one(a.fixture, a.today, a.state, a.executed,
                                  json.loads(a.plan), a.evidence),
                         ensure_ascii=False))
    else:
        sys.exit(main())
