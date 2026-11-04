# -*- coding: utf-8 -*-
"""T13 原型：用我们 SQLite 的某日数据当"今日"，驱动 rqalpha 模拟盘跑完整一天。

依赖同目录 `rqalpha_mod_t13sim.py`（rqalpha 官方 mod 扩展点，不改框架源码）。

    # 1) 用生产 data.db 的只读导出建本地 fixture（schema 与产品一致）
    python spikes/live_sim_t13.py init-db --csv <daily_bars.csv> --out <fixture.db>

    # 2) 跑一个模拟日（可反复、可跨进程续跑）
    python spikes/live_sim_t13.py day --fixture <db> --today 2026-10-27 \
        --state <dir> --plan '[{"action":"buy","symbol":"000792","volume":100}]'

    # 3) 一把梭验收（子进程串起 8 个场景，退出码 0 = 全绿）
    python spikes/live_sim_t13.py selftest

数据来源：生产 NEC 的 ~/paper-trading/data.db daily_bars（只读导出，2026-10-28）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parent
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

BUNDLE = os.environ.get("RQALPHA_BUNDLE", str(Path.home() / ".rqalpha" / "bundle"))
INIT_CASH = 100_000.0

_PLAN: list[dict] = []
_SNAP: dict = {"handler_dates": [], "orders": [], "positions": {}, "cash": None,
               "frozen_cash": None, "total_value": None, "before_trading": []}


def _to_obid(symbol: str) -> str:
    if symbol[0] in "69":
        return f"{symbol}.XSHG"
    return f"{symbol}.XSHE"


# ---------------------------------------------------------------------------
# 策略侧（喂给 rqalpha 的 user_funcs）
# ---------------------------------------------------------------------------
def _snapshot(context, tag: str) -> dict:
    from rqalpha.const import POSITION_DIRECTION

    stock = context.portfolio.stock_account
    out = {
        "tag": tag,
        "dt": str(context.now),
        "cash": round(float(stock.cash), 4),
        "frozen_cash": round(float(stock.frozen_cash), 4),
        "total_value": round(float(stock.total_value), 4),
        "positions": {},
    }
    for ob, dirs in list(stock._positions.items()):
        p = dirs[POSITION_DIRECTION.LONG]
        out["positions"][ob] = {
            "quantity": int(p.quantity),
            "closable": int(p.closable),
            "avg_price": round(float(p.avg_price), 4),
            "last_price": round(float(p.last_price), 4),
        }
    return out


def before_trading(context):
    _SNAP["before_trading"].append(_snapshot(context, "before_trading"))


def handle_bar(context, bar_dict):
    from rqalpha.api import order_shares

    _SNAP["handler_dates"].append(str(context.now)[:10])
    for act in _PLAN:
        if act["action"] not in ("buy", "sell") or not act.get("volume"):
            continue
        ob = _to_obid(act["symbol"])
        vol = act["volume"] * (1 if act["action"] == "buy" else -1)
        o = order_shares(ob, vol)
        if o is None:
            # 前端风控挡下（environment.py:134-137 can_submit_order=False）：API 不返回订单，
            # 拒单原因只在 user_log 里，必须由 mod 的事件监听补齐
            _SNAP["orders"].append({"symbol": ob, "side": "BUY" if vol > 0 else "SELL",
                                    "quantity": abs(vol), "status": "NOT_CREATED",
                                    "message": "order_shares returned None", "filled": 0,
                                    "avg_fill": None})
            continue
        orders = o if isinstance(o, (list, tuple)) else [o]
        for od in orders:
            _SNAP["orders"].append({
                "symbol": od.order_book_id,
                "side": str(getattr(od.side, "value", od.side)).upper(),
                "quantity": int(od.quantity),
                "status": str(getattr(od.status, "value", od.status)).upper(),
                "message": str(od.message), "filled": int(od.filled_quantity),
                "avg_fill": round(float(od.avg_price), 4) if od.filled_quantity else None,
            })
    _SNAP["post_bar"] = _snapshot(context, "after_handle_bar")


def after_trading(context):
    _SNAP["post_after"] = _snapshot(context, "after_trading")


# ---------------------------------------------------------------------------
# 会话编排
# ---------------------------------------------------------------------------
def build_config(fixture: str, today: str, state_dir: str, evidence: str,
                 force: bool, override_available: bool, bands: bool, persist: bool,
                 caliber: str, analyser: bool = False) -> dict:
    fingerprint = hashlib.sha256(json.dumps({
        "init_cash": INIT_CASH, "accounts": {"STOCK": INIT_CASH}, "fixture": str(Path(fixture).resolve()),
        "caliber": caliber, "bands": bands, "bundle": BUNDLE,
    }, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "base": {
            "start_date": today,
            "end_date": today,
            "accounts": {"STOCK": INIT_CASH},
            "frequency": "1d",
            "data_bundle_path": BUNDLE,
            "capital_gain_tax_rate": 0.0,
            "locale": "zh_CN",
            "run_type": "p",                       # PAPER_TRADING（仍走 sys_simulation）
            "persist": persist,
            "persist_mode": "real_time",
        },
        "extra": {"log_level": "none", "enable_profiler": False, "context_vars": {}},
        "mod": {
            "sys_progress": {"enabled": False},
            # 常驻线必须关掉 sys_analyser：跨进程续跑时昨日 SETTLEMENT 会重放
            # (executor.py:68-79)，_collect_daily 追加重复日记录，与 benchmark 序列
            # 长度不一致 -> rqrisk/risk.py:30 assert 炸 -> ExceptionGroup 让整个日会话
            # "已成交但退出失败"。指标由我们自己的 nav_history 记录器出。
            "sys_analyser": {"enabled": analyser, "benchmark": None, "plot": False,
                             "report_save_path": ""},
            "t13sim": {
                "enabled": True,
                "priority": 50,
                "db": str(Path(fixture).resolve()),
                "bundle": BUNDLE,
                "today": today,
                "state_dir": state_dir,
                "evidence": evidence,
                "force": force,
                "override_available": override_available,
                "bands": bands,
                "caliber": caliber,
                "fingerprint": fingerprint,
            },
        },
    }


def run_day(fixture: str, today: str, state_dir: str = "", plan: list | None = None,
            force: bool = False, override_available: bool = True, bands: bool = True,
            persist: bool = True, caliber: str = "raw", evidence_path: str = "",
            analyser: bool = False, force_persist: bool = False) -> dict:
    global _PLAN, _SNAP
    _PLAN = plan or []
    _SNAP = {"handler_dates": [], "orders": [], "positions": {}, "before_trading": []}

    from rqalpha import run_func

    # 无 state 目录就没有 persist provider 可用（rqalpha core 不带实现），
    # 此时必须关掉 persist，否则 main.py:77-79 直接抛 Missing persist provider
    persist = persist and (bool(state_dir) or force_persist)
    ev = evidence_path or os.path.join(tempfile.mkdtemp(prefix="t13ev_"), "mod.json")
    Path(ev).parent.mkdir(parents=True, exist_ok=True)
    config = build_config(fixture, today, state_dir, ev, force, override_available,
                          bands, persist, caliber, analyser)
    err = None
    n_trades = 0
    result = None
    try:
        result = run_func(config=config, before_trading=before_trading,
                          handle_bar=handle_bar, after_trading=after_trading)
    except Exception as exc:
        result = None
        err = f"{type(exc).__name__}: {str(exc)[:400]}"
    mod_ev = json.loads(Path(ev).read_text(encoding="utf-8")) if Path(ev).exists() else {}
    summary = {}
    if isinstance(result, dict):
        sa = result.get("sys_analyser") or {}
        if isinstance(sa, dict):
            summary = sa.get("summary") or {}
            tr = sa.get("trades")
            n_trades = 0 if tr is None else len(tr)
    return {
        "today": today,
        "plan": _PLAN,
        "error": err,
        "event_counts": mod_ev.get("event_counts", {}),
        "mod_trades": mod_ev.get("trades", []),
        "mod_orders": mod_ev.get("orders", []),
        "store_stats": mod_ev.get("store_stats", {}),
        "mod_exception": mod_ev.get("exception"),
        "orders": _SNAP.get("orders", []),
        "handler_dates": _SNAP.get("handler_dates", []),
        "before_trading": _SNAP.get("before_trading", []),
        "post_bar": _SNAP.get("post_bar"),
        "post_after": _SNAP.get("post_after"),
        "analyser": {k: summary.get(k) for k in ("start_date", "end_date", "total_value",
                                                 "starting_cash", "total_returns", "num_trades")
                     if isinstance(summary, dict) and k in summary},
        "n_analyser_trades": n_trades,
    }


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
def init_db(csv_path: str, out_path: str) -> int:
    conn = sqlite3.connect(out_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_bars (
        symbol TEXT NOT NULL, timestamp TEXT NOT NULL, open REAL NOT NULL,
        high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
        volume INTEGER NOT NULL, turn REAL DEFAULT 0.0,
        PRIMARY KEY (symbol, timestamp))""")
    n = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [(r["symbol"], r["timestamp"], float(r["open"]), float(r["high"]),
                 float(r["low"]), float(r["close"]), int(float(r["volume"])),
                 float(r.get("turn") or 0.0)) for r in csv.DictReader(f)]
        conn.executemany("INSERT OR REPLACE INTO daily_bars VALUES (?,?,?,?,?,?,?,?)", rows)
        n = len(rows)
    conn.commit()
    conn.close()
    return n


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
def _sub(runner: str, fixture: str, today: str, state: str, plan: list, **kw) -> tuple[int, dict, str]:
    cmd = [sys.executable, runner, "day", "--fixture", fixture, "--today", today,
           "--state", state, "--plan", json.dumps(plan, ensure_ascii=False)]
    for k, v in kw.items():
        cmd += [f"--{k.replace('_', '-')}", str(v).lower() if isinstance(v, bool) else str(v)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    payload = {}
    for line in reversed(p.stdout.splitlines()):
        if line.startswith("__T13__"):
            payload = json.loads(line[len("__T13__"):])
            break
    return p.returncode, payload, (p.stdout + p.stderr)[-6000:]


def _reason(out: dict, obid: str) -> str:
    """前端风控拒单原因（只存在于 ORDER_CREATION_REJECT 事件里）。"""
    hits = [o for o in out.get("mod_orders", []) if o.get("symbol") == obid
            and o.get("status") == "REJECTED_BY_VALIDATOR"]
    return hits[-1]["message"] if hits else ""


def selftest(fixture: str, runner: str) -> int:
    checks: list[tuple[str, bool, str]] = []

    def ck(name: str, ok: bool, detail: str = ""):
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))

    work = Path(tempfile.mkdtemp(prefix="t13_selftest_"))
    d1, d2 = "2026-10-27", "2026-10-28"
    sym = "000792"

    print("\n=== S1 bundle 过期时的行为（不覆盖 available_data_range） ===")
    rc, out, tail = _sub(runner, fixture, d2, "", [{"action": "hold", "symbol": sym, "volume": 0}],
                         **{"override_available": False})
    ck("S1 不覆盖窗口 -> rqalpha 大声拒绝（bundle 之后没有指数 bar，'今日'进不来）",
       bool(out.get("error")) and out.get("handler_dates") == [],
       f"error={str(out.get('error'))[:90]}")
    rc, out, tail = _sub(runner, fixture, d2, "", [{"action": "hold", "symbol": sym, "volume": 0}])
    ck("S1 覆盖 available_data_range -> bundle 之外的新鲜交易日可跑（适配层职责）",
       out.get("handler_dates") == [d2] and not out.get("error"),
       f"handler_dates={out.get('handler_dates')} error={str(out.get('error'))[:80]}")

    print("\n=== S2 一个完整模拟日（覆盖 available_data_range） ===")
    rc, out, tail = _sub(runner, fixture, d2, "", [{"action": "hold", "symbol": sym, "volume": 0}])
    ev = out.get("event_counts", {})
    ck("S2 空转门禁：handle_bar 至少调用一次", out.get("handler_dates") == [d2],
       f"{out.get('handler_dates')}")
    ck("S2 四阶段事件各触发一次（before/bar/after/settlement）",
       all(ev.get(k, 0) == 1 for k in ("BEFORE_TRADING", "BAR", "AFTER_TRADING", "SETTLEMENT")),
       json.dumps(ev))

    print("\n=== S3 买入成交 + 当日 T+1 冻结（含日内卖出被拒） ===")
    st3 = str(work / "state_s3")
    plan3 = [{"action": "buy", "symbol": sym, "volume": 100},
             {"action": "sell", "symbol": sym, "volume": 100}]
    rc, out, tail = _sub(runner, fixture, d1, st3, plan3)
    trades = out.get("mod_trades", [])
    orders = out.get("orders", [])
    pb = out.get("post_bar") or {}
    ck("S3 买入成交 1 笔（原始价撮合）", len(trades) == 1 and trades[0]["volume"] == 100,
       json.dumps(trades, ensure_ascii=False))
    sell_rej = [o for o in orders if o["side"] == "SELL"]
    r3 = _reason(out, _to_obid(sym))
    ck("S3 当日卖出被 T+1 挡下（前端风控，订单根本没被创建）",
       bool(sell_rej) and sell_rej[0]["status"] == "NOT_CREATED"
       and any(k in r3 for k in ("closable", "可平仓", "仓位不足")),
       f"api={sell_rej[0]['status'] if sell_rej else '-'} reason={r3[:90]}")
    pos = (pb.get("positions") or {}).get(_to_obid(sym), {})
    ck("S3 持仓 100 且 closable=0（T+1 冻结态）",
       pos.get("quantity") == 100 and pos.get("closable") == 0, json.dumps(pos))
    ck("S3 现金减少 = 成交额+费用", (out.get("before_trading") or [{}])[0].get("cash", 0)
       > (pb.get("cash") or 0),
       f"{(out.get('before_trading') or [{}])[0].get('cash')} -> {pb.get('cash')}")

    print("\n=== S4 跨进程续跑 + 次日解冻卖出 ===")
    plan4 = [{"action": "sell", "symbol": sym, "volume": 100}]
    rc, out, tail = _sub(runner, fixture, d2, st3, plan4)
    bt = (out.get("before_trading") or [{}])[0]
    ck("S4 续跑读到昨日持仓（重启进程后账本在）",
       bt.get("positions", {}).get(_to_obid(sym), {}).get("quantity") == 100,
       json.dumps(bt.get("positions")))
    ck("S4 T+1 解冻：before_trading 时 closable 已 =100",
       bt.get("positions", {}).get(_to_obid(sym), {}).get("closable") == 100,
       json.dumps(bt.get("positions", {}).get(_to_obid(sym))))
    ck("S4 卖出成交 1 笔", len(out.get("mod_trades", [])) == 1,
       json.dumps(out.get("mod_trades", []), ensure_ascii=False))
    ck("S4 卖出后现金回笼、持仓清零",
       (out.get("post_after") or {}).get("cash", 0) > bt.get("cash", 0)
       and (out.get("post_after") or {}).get("positions", {}).get(_to_obid(sym), {}).get("quantity") == 0,
       f"cash {bt.get('cash')} -> {(out.get('post_after') or {}).get('cash')}")
    ck("S4 昨日 SETTLEMENT 被重放一次（executor.py:68-79 的跨进程重复结算）",
       out.get("event_counts", {}).get("SETTLEMENT", 0) >= 1,
       json.dumps(out.get("event_counts")))

    print("\n=== S5 同日重跑幂等闸（fail-closed） ===")
    st5 = str(work / "state_s5")
    _sub(runner, fixture, d1, st5, [{"action": "buy", "symbol": sym, "volume": 100}])
    rc2, out2, tail2 = _sub(runner, fixture, d1, st5, [{"action": "buy", "symbol": sym, "volume": 100}])
    ck("S5 同日重跑被拒（幂等闸生效）", rc2 != 0 and "GATE" in (out2.get("error") or tail2),
       (out2.get("error") or "")[:120])
    rc3, out3, tail3 = _sub(runner, fixture, d1, st5, [{"action": "buy", "symbol": sym, "volume": 100}],
                            force="true")
    n = len(out3.get("mod_trades", []))
    ck("S5 --force 绕过闸后同日重复成交（证明 at-least-once 副作用真实存在）", n == 1,
       f"本次进程成交 {n} 笔（累加进昨日持仓 => 重复建仓）")
    ck("S5 重复建仓可见：持仓 200",
       ((out3.get("post_bar") or {}).get("positions") or {}).get(_to_obid(sym), {}).get("quantity") == 200,
       json.dumps(((out3.get("post_bar") or {}).get("positions"))))

    print("\n=== S6 涨跌停带来自我们的 store：一字板买不进 ===")
    d3 = "2026-06-23"
    ctrl_sym = "000651"      # 对照组用低价票：600519 一手要 15 万，会先被现金风控挡下
    plan6 = [{"action": "buy", "symbol": "002648", "volume": 100},
             {"action": "buy", "symbol": ctrl_sym, "volume": 100}]
    rc, out, tail = _sub(runner, fixture, d3, "", plan6)
    rej = [o for o in out.get("orders", []) if o["symbol"] == "002648.XSHE"]
    ctrl = [o for o in out.get("orders", []) if o["symbol"] == _to_obid(ctrl_sym)]
    ck("S6 涨停日买单被拒（band 由我们的 store 供给，撮合前判定在 rqalpha 内）",
       bool(rej) and "REJECTED" in rej[0]["status"] and "limit" in rej[0]["message"].lower(),
       f"{rej[0]['status']} / {rej[0]['message'][:90]}" if rej else "无单")
    ck("S6 同日对照组正常成交（不是整体不撮合）",
       bool(ctrl) and ctrl[0]["filled"] == 100, json.dumps(ctrl[:1]))

    print("\n=== S7 数据缺 limit 带 = 闸门 fail-open（无带对照） ===")
    rc, out, tail = _sub(runner, fixture, d3, "", [{"action": "buy", "symbol": "002648", "volume": 100}],
                         bands="false")
    o7 = (out.get("orders") or [{}])[0]
    ck("S7 limit_up/limit_down 缺失时涨停照样成交（fail-open，需 L2 sanity 兜底）",
       o7.get("filled") == 100, f"{o7.get('status')} filled={o7.get('filled')}")

    print("\n=== S8 新鲜日缺该标的 bar：拒单不是空转 ===")
    missing = None
    conn = sqlite3.connect(f"file:{fixture}?mode=ro", uri=True)
    for (s,) in conn.execute("select distinct symbol from daily_bars"):
        r = conn.execute("select 1 from daily_bars where symbol=? and timestamp like '2026-10-28%'", (s,)).fetchone()
        if not r:
            missing = s
            break
    conn.close()
    if missing:
        rc, out, tail = _sub(runner, fixture, d2, "", [{"action": "buy", "symbol": missing, "volume": 100}])
        o8 = (out.get("orders") or [{}])[0]
        r8 = _reason(out, _to_obid(missing))
        ck("S8 无 bar 的标的：不成交且给出可读原因（前端风控，订单未创建）",
           o8.get("filled") == 0 and bool(r8),
           f"{missing}: api={o8.get('status')} reason={r8[:80]}")
    else:
        ck("S8 无 bar 的标的下单被拒", False, "fixture 里每个 symbol 都有 10-28 数据")

    print("\n=== S9 persist=true 而 core 没有 persist provider（断点续跑的缺口） ===")
    # rqalpha 6.4.2 只带 AbstractPersistProvider 抽象(interface.py:643)，配置 persist=true
    # 却没有 provider -> main.py:77-79 大声抛 "Persist provider 未加载"。
    # 必须独立进程跑：进程内重入第二个 rqalpha 会话时，这条真因会被
    # cleanup_resources(main.py:301-307) 置空 Environment 单例后的
    # logger.py:38 EnvironmentNotInitialized 掩盖（见 report-t13 §5.6）。
    rc9, out9, tail9 = _sub(runner, fixture, d2, "", [], **{"force_persist": True})
    e9 = str(out9.get("error") or "") + tail9
    ck("S9 无 provider 时 persist 起不来（断点续跑必须自带 provider）",
       "Persist provider" in e9 and out9.get("handler_dates") in ([], None),
       f"rc={rc9} payload_err={str(out9.get('error'))[:60]} "
       f"masked_by_env_not_init={'EnvironmentNotInitialized' in tail9}")

    failed = [c for c in checks if not c[1]]
    print(f"\n==== T13 selftest: {len(checks) - len(failed)}/{len(checks)} PASS ====")
    for name, _, detail in failed:
        print(f"  FAILED: {name}\n    {detail}")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)

    a = sp.add_parser("init-db")
    a.add_argument("--csv", required=True)
    a.add_argument("--out", required=True)

    d = sp.add_parser("day")
    d.add_argument("--fixture", required=True)
    d.add_argument("--today", required=True)
    d.add_argument("--state", default="")
    d.add_argument("--plan", default="[]")
    d.add_argument("--force", type=lambda x: str(x).lower() in ("1", "true", "yes"), default=False)
    d.add_argument("--override-available", dest="override_available",
                   type=lambda x: str(x).lower() in ("1", "true", "yes"), default=True)
    d.add_argument("--bands", type=lambda x: str(x).lower() in ("1", "true", "yes"), default=True)
    d.add_argument("--persist", type=lambda x: str(x).lower() in ("1", "true", "yes"), default=True)
    d.add_argument("--caliber", default="raw", choices=["raw", "qfq"])
    d.add_argument("--analyser", type=lambda x: str(x).lower() in ("1", "true", "yes"), default=False)
    d.add_argument("--force-persist", dest="force_persist",
                   type=lambda x: str(x).lower() in ("1", "true", "yes"), default=False)
    d.add_argument("--evidence", default="")
    d.add_argument("--quiet", action="store_true")

    s = sp.add_parser("selftest")
    s.add_argument("--fixture", required=True)

    args = ap.parse_args()
    if args.cmd == "init-db":
        n = init_db(args.csv, args.out)
        print(f"fixture ready: {args.out} rows={n}")
        return 0
    if args.cmd == "selftest":
        return selftest(args.fixture, str(Path(__file__).resolve()))
    out = run_day(args.fixture, args.today, args.state, json.loads(args.plan), args.force,
                  args.override_available, args.bands, args.persist, args.caliber,
                  args.evidence, args.analyser, args.force_persist)
    print("__T13__" + json.dumps(out, ensure_ascii=False))
    if not args.quiet:
        print(json.dumps({k: out[k] for k in ("today", "error", "event_counts", "orders",
                                              "handler_dates", "mod_trades")},
                         ensure_ascii=False, indent=1))
    return 1 if out["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
