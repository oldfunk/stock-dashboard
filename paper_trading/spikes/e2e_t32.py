"""T32 端到端验收：生产真实原始价 → 产品适配器 → rqalpha 模拟线（含双轨影子对账）。

数据来自 NEC 生产（全部只读导出，脚本本身不写生产库）：

- `/tmp/t32_raw.csv`：`ak.stock_zh_a_daily(adjust="")` 对生产池 38 只的原始价日线
  （2025-06-03→2026-10-28，含 amount 成交额）；
- `/tmp/t32_mother_ref.csv`：母库 `kline_daily` 的 `close_raw/volume/amount`
  （独立验料源，同窗口）。

用法（需要装了 rqalpha 的 venv；产品 venv 没装就跑 `build` 也能验 T31 管道）：

    python spikes/e2e_t32.py build --raw-csv ... --ref-csv ... --out-db data_e2e.db
    python spikes/e2e_t32.py verify --db data_e2e.db --bundle ~/.rqalpha/bundle \
        --work <空目录>
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _bars_from_csv(path: str) -> dict:
    out = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"]].append(r)
    return out


def build(raw_csv: str, ref_csv: str, out_db: str) -> int:
    """走产品管道：口径对照 → 拒收/落库 → 新鲜度。"""
    sys.path.insert(0, str(REPO))
    from paper_trading.data import raw_feed
    from paper_trading.data.db_manager import DataDBManager
    from paper_trading.models import Bar

    refs = defaultdict(dict)
    with open(ref_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["close_raw"]:
                refs[r["symbol"]][r["date"]] = {"close_raw": float(r["close_raw"]),
                                                "volume": r["volume"], "amount": r["amount"]}

    db = DataDBManager(out_db)
    tally = {"written": 0, "rejected": 0, "mismatch_rows": 0, "no_ref": 0, "checked_rows": 0}
    rejected_detail = []
    for sym, rows in _bars_from_csv(raw_csv).items():
        bars = [Bar(symbol=sym, timestamp=datetime.fromisoformat(r["date"]),
                    open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                    close=float(r["close"]), volume=int(r["volume"]),
                    amount=float(r["amount"] or 0) or None, adjust="raw") for r in rows]
        v = raw_feed.check_raw_bars(bars, refs.get(sym) or {})
        r = raw_feed.sync_symbol(sym, bars, v, db)
        tally["written"] += r["written"]
        tally["no_ref"] += v["no_reference"]
        tally["checked_rows"] += v["checked"]
        tally["mismatch_rows"] += len(v["mismatches"])
        if r["status"] == "rejected":
            tally["rejected"] += 1
            rejected_detail.append({"symbol": sym, "sample": v["mismatches"][:2],
                                    "absurd": v["absurd"][:2]})
    print(json.dumps({"tally": tally, "rejected_symbols": rejected_detail[:5],
                      "rejected_count": tally["rejected"]}, ensure_ascii=False, indent=1))
    print("FRESHNESS " + json.dumps(raw_feed.raw_freshness(
        db, sorted({s for s in _bars_from_csv(raw_csv)}), today=date(2026, 10, 28)),
        ensure_ascii=False)[:300])
    return 0


def _cli(py: str, db: str, args: list) -> tuple:
    cmd = [py, "-m", "paper_trading.cli", "--data-db", db] + args
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    payload = {}
    txt = p.stdout.strip()
    try:
        payload = json.loads(txt)                       # emit() 默认多行美化输出
    except json.JSONDecodeError:
        for line in reversed(p.stdout.splitlines()):    # 兜底：找最后一行 JSON
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    if isinstance(payload, dict) and "data" in payload:
        payload = dict(payload.get("data") or {}) | {"_error": payload.get("error")}
    return p.returncode, payload, (p.stdout + p.stderr)[-1500:]


def _cli_json(py: str, db: str, args: list) -> tuple:
    return _cli(py, db, args + ["--json"])


def verify(db: str, bundle: str, work: Path, py: str) -> int:
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {str(detail)[:200]}" if detail else ""))

    work.mkdir(parents=True, exist_ok=True)
    st_dir = work / "state"

    print("\n=== E1 产品 CLI 状态体检（不跑引擎） ===")
    rc, payload, tail = _cli(py, db, ["rq", "state", "--state-dir", str(st_dir),
                                     "--symbols", "000792", "600519", "002648",
                                     "--today", "2026-10-28"])
    store = payload.get("store") or {}
    ck("E1 store 构建通过（G-T13-3 band 门禁未开火）", rc == 0 and "gate_failed" not in store,
       store.get("gate_failed", ""))
    ck("E1 每只标的都带 band（bands_ok = bars-1）",
       all(v["bands_ok"] == v["bars"] - 1 for v in (store.get("stats") or {}).values()),
       json.dumps({k: (v["bars"], v["bands_ok"]) for k, v in (store.get("stats") or {}).items()}))

    print("\n=== E2 模拟日：买入成交 + 当日 T+1 拒卖（一天一进程） ===")
    plan2 = json.dumps([{"action": "buy", "symbol": "000792", "volume": 100},
                        {"action": "sell", "symbol": "000792", "volume": 100}])
    rc, p2, tail2 = _cli_json(py, db, ["rq", "day", "--today", "2026-10-27",
                                       "--symbols", "000792", "600519",
                                       "--plan", plan2, "--state-dir", str(st_dir),
                                       "--bundle", bundle, "--workdir", str(work / "ev")])
    gates2 = (p2 or {}).get("gates") or {}
    trades2 = ((p2 or {}).get("evidence") or {}).get("trades") or []
    rejects2 = ((p2 or {}).get("evidence") or {}).get("rejects") or []
    ck("E2 会话成功且门禁全绿", rc == 0 and gates2.get("ok") and not (p2 or {}).get("error"),
       f"rc={rc} err={(p2 or {}).get('error')} errors={gates2.get('errors')}")
    ck("E2 买入成交 1 笔（原始价撮合）", len(trades2) == 1 and trades2[0]["side"] == "BUY",
       json.dumps(trades2, ensure_ascii=False))
    ck("E2 当日卖出被 T+1 挡下且原因可读（G-T13-8）",
       any("仓位不足" in r.get("reason", "") or "closable" in r.get("reason", "").lower()
           for r in rejects2), json.dumps(rejects2, ensure_ascii=False)[:220])
    post = (p2 or {}).get("snapshots") or [{}]
    pos = (post[-1].get("positions") or {}).get("000792.XSHE") or {}
    ck("E2 持仓 100、closable=0（冻结态落盘）",
       pos.get("quantity") == 100 and pos.get("closable") == 0, json.dumps(pos))
    ck("E2 会话闸标记完成", (p2 or {}).get("session_marked_done") is True,
       json.dumps((p2 or {}).get("session_marked_done")))

    print("\n=== E3 跨进程续跑：解冻并卖出 ===")
    plan3 = json.dumps([{"action": "sell", "symbol": "000792", "volume": 100}])
    rc, p3, tail3 = _cli_json(py, db, ["rq", "day", "--today", "2026-10-28",
                                       "--symbols", "000792", "600519",
                                       "--plan", plan3, "--state-dir", str(st_dir),
                                       "--bundle", bundle, "--workdir", str(work / "ev")])
    trades3 = ((p3 or {}).get("evidence") or {}).get("trades") or []
    snaps = (p3 or {}).get("snapshots") or []
    before = snaps[0] if snaps else {}
    ck("E3 续跑读到昨日持仓且已解冻（closable=100）",
       (before.get("positions") or {}).get("000792.XSHE", {}).get("closable") == 100,
       json.dumps(before.get("positions")))
    ck("E3 卖出成交 1 笔", len(trades3) == 1 and trades3[0]["side"] == "SELL",
       json.dumps(trades3, ensure_ascii=False))
    ck("E3 昨日 SETTLEMENT 重放被门禁点出（G-T13-6 警告）",
       any("重放昨日结算" in w for w in ((p3 or {}).get("gates") or {}).get("warnings", [])),
       json.dumps((p3 or {}).get("gates") or {}))

    print("\n=== E4 幂等闸：同日重跑必须被拒 ===")
    rc4, p4, tail4 = _cli_json(py, db, ["rq", "day", "--today", "2026-10-28",
                                        "--symbols", "000792", "600519",
                                        "--plan", plan3, "--state-dir", str(st_dir),
                                        "--bundle", bundle, "--workdir", str(work / "ev")])
    ck("E4 同日重跑被会话闸拒绝（exit≠0）", rc4 != 0 and "同日重跑" in (tail4 or ""),
       f"rc={rc4}")

    print("\n=== E5 涨跌停带来自产品 store：一字板买不进 + 同日对照成交 ===")
    st5 = work / "state_band"
    plan5 = json.dumps([{"action": "buy", "symbol": "002648", "volume": 100},
                        {"action": "buy", "symbol": "000651", "volume": 100}])
    rc5, p5, tail5 = _cli_json(py, db, ["rq", "day", "--today", "2026-06-23",
                                        "--symbols", "002648", "000651",
                                        "--plan", plan5, "--state-dir", str(st5),
                                        "--bundle", bundle, "--workdir", str(work / "ev5")])
    rej5 = [o for o in ((p5 or {}).get("orders") or []) if o["symbol"] == "002648.XSHE"]
    ctrl5 = [o for o in ((p5 or {}).get("orders") or []) if o["symbol"] == "000651.XSHE"]
    ck("E5 涨停日拒单且原因可读", bool(rej5) and "limit" in rej5[0].get("message", "").lower(),
       json.dumps(rej5[:1], ensure_ascii=False))
    ck("E5 同日对照组成交（不是整体不撮合）",
       bool(ctrl5) and ctrl5[0].get("filled") == 100, json.dumps(ctrl5[:1], ensure_ascii=False))

    print("\n=== E6 双轨影子对账（rqalpha vs 旧 PaperBroker，同日同输入） ===")
    sd = work / "shadow"
    if sd.exists():
        shutil.rmtree(sd)
    rc6, p6, tail6 = _cli_json(py, db, ["rq", "shadow-day", "--today", "2026-10-27",
                                         "--symbols", "000792", "600519",
                                         "--plan", json.dumps([{"action": "buy", "symbol": "000792",
                                                                "volume": 100}]),
                                         "--shadow-dir", str(sd), "--bundle", bundle])
    diff = (p6 or {}).get("diff") or {}
    ck("E6 影子对照跑通（两侧都有账）",
       rc6 in (0, 1) and (p6 or {}).get("rqalpha", {}).get("cash") is not None
       and (p6 or {}).get("legacy", {}).get("cash") is not None,
       f"rc={rc6} rq={(p6 or {}).get('rqalpha', {}).get('cash')} "
       f"legacy={(p6 or {}).get('legacy', {}).get('cash')}")
    ck("E6 持仓一致、现金/费用差在容差内（无 violations）",
       not diff.get("violations"), json.dumps(diff, ensure_ascii=False)[:400])
    ck("E6 影子账本独立于 data.db 与生产账本",
       (p6 or {}).get("shadow_dir") == str(sd) and
       not (sd / "paper_account.db").resolve().is_relative_to(Path(db).resolve().parent),
       f"shadow_dir={(p6 or {}).get('shadow_dir')} db_parent={Path(db).resolve().parent}")

    failed = [c for c in checks if not c[1]]
    print(f"\n==== T32 e2e: {len(checks) - len(failed)}/{len(checks)} PASS ====")
    for name, _, detail in failed:
        print(f"  FAILED: {name} :: {detail}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--raw-csv", required=True)
    b.add_argument("--ref-csv", required=True)
    b.add_argument("--out-db", required=True)
    v = sp.add_parser("verify")
    v.add_argument("--db", required=True)
    v.add_argument("--bundle", required=True)
    v.add_argument("--work", required=True)
    v.add_argument("--python", default=sys.executable)
    args = ap.parse_args()
    if args.cmd == "build":
        return build(args.raw_csv, args.ref_csv, args.out_db)
    return verify(args.db, str(Path(args.bundle).expanduser()), Path(args.work), args.python)


if __name__ == "__main__":
    sys.exit(main())
