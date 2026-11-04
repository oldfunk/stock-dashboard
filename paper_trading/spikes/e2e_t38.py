"""T38 端到端：影子留痕默认写 + 过户费进引擎后 `fee_delta` 归零（真实 rqalpha 会话）。

三件事各自独立可核（每个场景一个子进程，G-T13-9 一天一进程）：

- **S1 默认留痕**：`rq shadow-day` 不带任何旗 → 生产账户库出现
  一行 `op_log[parity:diff]`，且两侧费用**逐分相等**（`fee_delta=0.0`）、
  evidence 里过户费 overlay 确实装在 CS 上；
- **S2 关闭旗**：`--no-op-log` → 同样的对账结果，但 op_log 一行都不写；
- **S3 变异检查**：子进程里把 `costs.install_transfer_fee` 打成空操作
  （模拟"框架改了注册方式 / overlay 没装上"），必须同时看到
  ①后置门禁 `TransferFeeNotInstalled` 停线、②`fee_delta` 立刻变正数
  ——反证 S1 的"归零"不是巧合，也不是容差放宽蒙过去的。

金额刻意放大到 22 万元（2 万股 × 11 元）：T29/T32 当年的 100 股单子买卖都
撞上 5 元最低佣金，把佣金口径差盖住了；这个量级下佣金差是 55 vs 176 元，
遮不住。

用法（需要装了 rqalpha 的 venv）：

    python spikes/e2e_t38.py run --py <python> --bundle ~/.rqalpha/bundle --work <空目录>
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SYM, DAY, PRICE, PREV, VOL = "000792", "2026-10-28", 11.00, 10.50, 20000
AMOUNT = PRICE * VOL                                   # 22 万元

CHECKS: list = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  <- {detail}" if detail else ""))


def _build_scratch(work: Path) -> str:
    """合成 raw 日线（自洽 OHLC + 前收在 ±10% 带内），只写 scratch 库。"""
    sys.path.insert(0, str(REPO))
    from paper_trading.data.db_manager import DataDBManager
    from paper_trading.models import Bar

    db_path = str(work / "data.db")
    if Path(db_path).exists():
        Path(db_path).unlink()
    db = DataDBManager(db_path)
    bars = []
    for i, d in enumerate(["2026-10-26", "2026-10-27", DAY]):
        c = [10.40, PREV, PRICE][i]
        bars.append(Bar(symbol=SYM, timestamp=datetime.fromisoformat(d + "T00:00:00"),
                        open=round(c - 0.1, 2), high=round(c + 0.1, 2),
                        low=round(c - 0.2, 2), close=c, volume=1_000_000,
                        amount=c * 1_000_000, adjust="raw"))
    assert db.upsert_raw_bars(bars, source="e2e-t38") == 3, "落库失败"
    return db_path


def _cli(py: str, args: list) -> tuple:
    cmd = [py, "-m", "paper_trading.cli"] + args
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, _parse_json(p.stdout), p.stdout[-400:] + "\n" + p.stderr[-400:]


def _parse_json(text: str) -> dict:
    """从 stdout 里捞出 emit() 的多行 JSON（rqalpha 的 DEBUG 行会混在同一份输出里）。"""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and ("data" in obj or "diff" in obj):
            return obj.get("data") if isinstance(obj.get("data"), dict) else obj
    return {}


def _oplog_rows(account_db: str) -> list:
    if not Path(account_db).exists():
        return []
    with sqlite3.connect(account_db) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT action, ok, params, result FROM op_log").fetchall()]


def _global(db: str, account_db: str, work: Path) -> list:
    """全局参数：scratch 的 data/账户库/配置——绝不指向现网 `paper_account.db`。"""
    return ["--data-db", db, "--account-db", account_db,
            "--config", str(work / "config.yaml")]


def run(py: str, bundle: str, work: Path) -> int:
    work.mkdir(parents=True, exist_ok=True)
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    (work / "config.yaml").write_text(
        "stock_pool:\n  - \"%s\"\n\naccount:\n  initial_cash: 1000000.0\n\n"
        "trading:\n  commission_rate: 0.00025\n  commission_min: 5.0\n"
        "  stamp_duty_rate: 0.0005\n  transfer_fee_rate: 0.00001\n"
        "  slippage_fixed: 0.01\n  slippage_pct: 0.001\n  use_slippage_pct: false\n"
        % SYM, encoding="utf-8")
    db = _build_scratch(work)
    plan = json.dumps([{"action": "buy", "symbol": SYM, "volume": VOL}])

    print(f"\n=== S1 默认留痕 + fee_delta 归零（{SYM} 买 {VOL} 股 @ {PRICE} = {AMOUNT:,.0f} 元）===")
    sd1 = work / "shadow1"
    acc1 = str(work / "acct1.db")
    rc1, p1, t1 = _cli(py, _global(db, acc1, work) + [
        "rq", "shadow-day", "--today", DAY, "--symbols", SYM, "--plan", plan,
        "--shadow-dir", str(sd1), "--bundle", bundle, "--init-cash", "1000000"])
    diff1 = (p1 or {}).get("diff") or {}
    rq1 = (p1 or {}).get("rqalpha") or {}
    lg1 = (p1 or {}).get("legacy") or {}
    ev1 = (rq1.get("evidence") or {})
    cost1 = rq1.get("cost") or ev1.get("cost") or {}
    ck("S1 会话跑通（无 error、两侧都有账）",
       rq1.get("cash") is not None and lg1.get("cash") is not None and rq1.get("error") is None,
       f"rc={rc1} rq_cash={rq1.get('cash')} legacy_cash={lg1.get('cash')} "
       f"err={rq1.get('error') or t1[-200:]}")
    fills = rq1.get("fills") or []
    ck("S1 真成交（否则费用=0，对账无意义）", len(fills) == 1,
       json.dumps(fills, ensure_ascii=False)[:200])
    ck("S1 fee_delta 归零（不是容差放宽）", diff1.get("fee_delta") == 0.0,
       f"fee_delta={diff1.get('fee_delta')} rq_fees={rq1.get('fees')} "
       f"legacy_fees={lg1.get('fees')}")
    ck("S1 佣金没被最低佣金盖住（金额够大才有证明力）",
       (lg1.get("fees") or 0) > 10, f"legacy_fees={lg1.get('fees')}")
    ck("S1 过户费 overlay 装在 CS 上并真的收过费",
       cost1.get("wrapped") == ["CS"] and any(d["applied"] > 0 for d in cost1.get("deciders") or []),
       json.dumps(cost1, ensure_ascii=False)[:250])
    ck("S1 持仓/T+1 一致、无 violations、exit 0",
       bool(rq1) and not diff1.get("violations") and rc1 == 0,
       json.dumps(diff1, ensure_ascii=False)[:250])
    rows1 = [r for r in _oplog_rows(acc1) if r["action"] == "parity:diff"]
    ck("S1 默认落 op_log[parity:diff]（裁决 4：不留痕就是没验收证据）",
       len(rows1) == 1 and json.loads(rows1[0]["result"] or "{}").get("fee_delta") == 0.0,
       json.dumps(rows1, ensure_ascii=False, default=str)[:300])
    ck("S1 留痕只写审计、账户余额/持仓未被影子改动",
       rows1 and not [r for r in _oplog_rows(acc1)
                      if r["action"] in ("buy", "sell", "agent:tick")],
       json.dumps([r["action"] for r in _oplog_rows(acc1)], ensure_ascii=False))

    print("\n=== S2 --no-op-log 关闭旗 ===")
    sd2 = work / "shadow2"
    acc2 = str(work / "acct2.db")
    rc2, p2, t2 = _cli(py, _global(db, acc2, work) + [
        "rq", "shadow-day", "--today", DAY, "--symbols", SYM, "--plan", plan,
        "--shadow-dir", str(sd2), "--bundle", bundle, "--init-cash", "1000000",
        "--no-op-log"])
    diff2 = (p2 or {}).get("diff") or {}
    ck("S2 对账结果与 S1 相同（fee_delta 仍为 0）", diff2.get("fee_delta") == 0.0,
       f"rc={rc2} diff={json.dumps(diff2, ensure_ascii=False)[:200]} err={t2[-150:]}")
    ck("S2 一条 op_log 都不写", _oplog_rows(acc2) == [],
       json.dumps(_oplog_rows(acc2), ensure_ascii=False, default=str)[:250])

    print("\n=== S3 变异检查：overlay 装不上时必须停线且 fee_delta 变正 ===")
    mutate = work / "mutate.py"
    mutate.write_text(
        "import json, sys, pathlib\n"
        "sys.path.insert(0, r\"%s\")\n"
        "from paper_trading.integration.rq_engine import costs, shadow\n"
        "costs.install_transfer_fee = lambda env, rate=0.0, **kw: []   # 模拟没装上\n"
        "res = shadow.shadow_day(today=\"%s\", data_db=r\"%s\", bundle=r\"%s\",\n"
        "                      symbols=[\"%s\"], plan=%s,\n"
        "                      shadow_dir=r\"%s\", init_cash=1000000.0,\n"
        "                      fee={'commission_rate': 0.00025, 'commission_min': 5.0,\n"
        "                           'stamp_duty_rate': 0.0005, 'transfer_fee_rate': 0.00001})\n"
        "gate = res['rqalpha'].get('gates') or {}\n"
        "pathlib.Path(r\"%s\").write_text(json.dumps(\n"
        "    {'fee_delta': res['diff']['fee_delta'], 'rq_fees': res['rqalpha'].get('fees'),\n"
        "     'legacy_fees': res['legacy'].get('fees'), 'violations': res['diff']['violations'],\n"
        "     'gate_errors': gate.get('errors'), 'ok': res['ok']}), encoding='utf-8')\n"
        % (str(REPO), DAY, db, bundle, SYM, plan, str(work / "shadow3"),
           str(work / "mutate.json"))
    )
    p3 = subprocess.run([py, str(mutate)], cwd=str(REPO), capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    m3 = json.loads((work / "mutate.json").read_text(encoding="utf-8")) \
        if (work / "mutate.json").exists() else {}
    ck("S3 变异场景跑完（子进程产出结果文件）", bool(m3),
       f"rc={p3.returncode} stderr={p3.stderr[-200:]}")
    ck("S3 后置门禁 TransferFeeNotInstalled 停线",
       any("TransferFeeNotInstalled" in e for e in m3.get("gate_errors") or []),
       json.dumps(m3.get("gate_errors"), ensure_ascii=False)[:250])
    ck("S3 fee_delta 变成 -2.2（引擎侧少收过户费，delta=rq-legacy 为负）",
       abs(abs(m3.get("fee_delta") or 0) - 2.2) < 0.001 and m3["rq_fees"] < m3["legacy_fees"],
       f"fee_delta={m3.get('fee_delta')} rq={m3.get('rq_fees')} "
       f"legacy={m3.get('legacy_fees')}")
    ck("S3 ok=False（缺口不会被静默放过）", m3.get("ok") is False,
       json.dumps(m3.get("violations"), ensure_ascii=False)[:250])

    print("\n=== S4 白名单：非 parity:diff 的 action 必须被拒 ===")
    wl = work / "whitelist.py"
    wl.write_text(
        "import json, sys, pathlib\n"
        "sys.path.insert(0, r\"%s\")\n"
        "from paper_trading.integration.rq_engine import shadow\n"
        "class B:\n"
        "    def __init__(self): self.calls = []\n"
        "    def log_operation(self, action, *a, **k): self.calls.append(action)\n"
        "res = {'today': '%s', 'ok': True, 'diff': {'position_delta': {}, 'violations': [],\n"
        "        'cash_delta': 0.0, 'fee_delta': 0.0, 'fill_delta': 0},\n"
        "       'rqalpha': {'cash': 1.0}, 'legacy': {'cash': 1.0}}\n"
        "out, b = [], B()\n"
        "for bad in ['buy', 'sell', 'data:migrate', 'parity:diff:x', '', None]:\n"
        "    try:\n"
        "        shadow.record_to(b, res, action=bad)\n"
        "        out.append([bad, 'ACCEPTED'])\n"
        "    except (ValueError, TypeError) as e:\n"
        "        out.append([bad, type(e).__name__])\n"
        "shadow.record_to(b, res)                 # 默认 action 必须仍然可写\n"
        "out.append(['default-wrote', b.calls])\n"
        "pathlib.Path(r\"%s\").write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')\n"
        % (str(REPO), DAY, str(work / "whitelist.json"))
    )
    p4 = subprocess.run([py, str(wl)], cwd=str(REPO), capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    w4 = json.loads((work / "whitelist.json").read_text(encoding="utf-8")) \
        if (work / "whitelist.json").exists() else []
    rejected = [x for x in w4 if x[0] not in ("default-wrote",)]
    ck("S4 六个非法/空 action 全部被拒且没落到 broker",
       len(rejected) == 6 and all(x[1] in ("ValueError", "TypeError") for x in rejected),
       json.dumps(w4, ensure_ascii=False)[:300])
    ck("S4 默认 action 正常写入（白名单不是把功能关掉）",
       bool(w4) and w4[-1][0] == "default-wrote" and w4[-1][1] == ["parity:diff"],
       json.dumps(w4[-1:], ensure_ascii=False)[:200] if w4 else f"rc={p4.returncode}")

    n_fail = sum(1 for c in CHECKS if not c["ok"])
    print(f"\n=== 汇总：{len(CHECKS) - n_fail}/{len(CHECKS)} PASS ===")
    (work / "e2e_t38_result.json").write_text(
        json.dumps(CHECKS, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if n_fail else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--py", default=sys.executable)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    return run(a.py, str(Path(a.bundle).expanduser()), Path(a.work))


if __name__ == "__main__":
    sys.exit(main())
