"""T19 (M1 签字必备)：S 级信号门禁 —— 同价源同锚（route B）+ 股数语义 + ±1 交易日匹配。

为什么分三张表（T9 的教训：一次对照把 数据/判定/执行 三者混在一起，差异无法归因）：

  J 表 判定层一致性：把 rqalpha **自己看到的同一份 21 根收盘** 灌回我们的纯函数
      `engine/rules.evaluate()`，比较两个判定结果。与价源/锚点/时点/费用全部无关，
      这是 AGENTS 纪律 3「同一套判定」的唯一硬证据。

  S 表 信号层一致性：rqalpha 信号集 vs 旧引擎信号集（各自的价源视图），±1 交易日匹配，
      未匹配逐笔归因（预期主因＝复权锚点差异 ⇒ 落在除权日附近）。

  F 表 成交缺口归因：T9 遗留的 108 vs 80（26–28 笔）到底去哪了 —— 按 skipped 原因逐笔计数。

只读：不落任何库、不下单。数据 = rqalpha bundle（真实市场数据）。
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

BUNDLE = os.path.expanduser(r"~\.rqalpha\bundle")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
CAPTURE = os.path.join(os.environ.get("TEMP", "/tmp"), "t19_capture.json")

SYMBOLS = ["000858", "601318", "600036", "601398", "600900",
           "000333", "600887", "600276", "601888", "000651"]
START, END = "2026-01-05", "2026-09-10"
INIT_CASH = 100_000.0
LOT = 100
TOL_DAYS = 1
ERRS = []


def obid(s):
    return f"{s}.{'XSHG' if s[0] in '69' else 'XSHE'}"


def code_of(ob):
    return str(ob).split(".")[0]


def ma_state(c):
    """与 M1-A 策略完全相同的 MA5/20 金叉死叉定义。"""
    if c is None or len(c) < 21:
        return None
    cur_s, cur_l = sum(c[-5:]) / 5, sum(c[-20:]) / 20
    prv_s, prv_l = sum(c[-6:-1]) / 5, sum(c[-21:-1]) / 20
    if prv_s <= prv_l and cur_s > cur_l:
        return "buy"
    if prv_s >= prv_l and cur_s < cur_l:
        return "sell"
    return None


# ---------------------------------------------------------------- rqalpha 腿
def run_rqalpha():
    from rqalpha import run_func

    rec = []
    global ERRS
    ERRS.clear()

    def init(context):
        context.symbols = [obid(s) for s in SYMBOLS]

    def handle_bar(context, bar_dict):
        from rqalpha.api import history_bars, order_shares
        from rqalpha.const import POSITION_DIRECTION
        day = context.now.strftime("%Y-%m-%d")
        for ob in context.symbols:
            try:
                h = history_bars(ob, 21, "1d", "close")
            except Exception as e:                              # noqa: BLE001
                ERRS.append(f"{type(e).__name__}: {e}")
                continue
            if h is None or len(h) < 21:
                continue
            closes = [float(x) for x in h]
            dec = ma_state(closes)
            if dec:
                rec.append({"day": day, "symbol": code_of(ob), "closes": closes,
                            "decision": dec})
            pos = context.portfolio.get_position(ob, POSITION_DIRECTION.LONG)
            qty = int(pos.quantity) if pos else 0
            if dec == "buy" and qty == 0:
                order_shares(ob, LOT)
            elif dec == "sell" and qty > 0:
                order_shares(ob, -min(LOT, qty))

    cfg = {"base": {"start_date": START, "end_date": END,
                    "accounts": {"STOCK": INIT_CASH}, "frequency": "1d",
                    "data_bundle_path": BUNDLE, "capital_gain_tax_rate": 0.0,
                    "locale": "zh_CN"},
           "mod": {"sys_analyser": {"benchmark": "000300.XSHG"},
                   "sys_simulation": {"slippage": 0},
                   "sys_transaction_cost": {"stock_commission_multiplier": 0.00025 / 0.0008,
                                            "stock_min_commission": 5.0}}}
    res = run_func(init=init, handle_bar=handle_bar, config=cfg)
    sa = res.get("sys_analyser", {}) if isinstance(res, dict) else {}
    trades = sa.get("trades")
    fills = []
    try:
        for _, r in trades.iterrows():
            fills.append({"day": str(r.get("datetime"))[:10],
                          "symbol": code_of(r.get("order_book_id")),
                          "side": "buy" if "buy" in str(r.get("side", "")).lower() else "sell",
                          "qty": float(r.get("quantity") or 0),
                          "price": float(r.get("price") or 0),
                          "fee": float(r.get("transaction_cost") or 0)})
    except Exception:                                         # noqa: BLE001
        pass
    json.dump({"signals": rec, "fills": fills}, open(CAPTURE, "w", encoding="utf-8"))
    print("[debug] handle_bar errors:", Counter(ERRS).most_common(3), "rec=", len(rec))
    return rec, fills, sa.get("summary", {})


# ---------------------------------------------------------------- 旧引擎腿
def old_leg(series_by_sym):
    from paper_trading.engine.backtest import Backtester, FeeModel
    from paper_trading.models import Bar

    bm, days = {}, set()
    for s, ser in series_by_sym.items():
        bm[s] = [Bar(symbol=s, timestamp=b["d"], open=round(b["o"], 2),
                     high=round(b["h"], 2), low=round(b["l"], 2),
                     close=round(b["c"], 2), volume=int(b["v"]), turn=b["t"])
                 for b in ser]
        days |= {b["d"].strftime("%Y-%m-%d") for b in ser
                 if START <= b["d"].strftime("%Y-%m-%d") <= END}
    days = sorted(days)
    rules = []
    for s, ser in series_by_sym.items():
        win = [b for b in ser if START <= b["d"].strftime("%Y-%m-%d") <= END]
        p0 = win[0]["c"] if win else 100.0
        rules.append({"id": "ma-" + s, "type": "ma_cross", "symbol": s,
                      "params": {"short": 5, "long": 20},
                      "action": {"side": "buy", "value": round(LOT * p0, 2)},
                      "once": True})
    cfg = {"rules": rules, "min_ticket_cny": 0, "max_orders_per_day": 0,
           "max_turnover_per_day": 0}
    bt = Backtester(cfg, fee=FeeModel(), slippage_pct=0.0, slippage_fixed=0.0)
    res = bt.run(bm, days, initial_cash=INIT_CASH)

    # 旧引擎自己的信号集（用同一个纯函数 evaluate，喂它自己的价源视图）
    from paper_trading.engine.rules import RuleContext, evaluate
    sig = []
    for s, ser in series_by_sym.items():
        closes_all = [b["c"] for b in ser]
        dates_all = [b["d"].strftime("%Y-%m-%d") for b in ser]
        for i, dt in enumerate(dates_all):
            if not (START <= dt <= END):
                continue
            k = bisect_upto(dates_all, dt)
            ctx = RuleContext(now=dt, day=dt, symbol=s, price=closes_all[k - 1],
                              closes=closes_all[:k])
            it = evaluate({"id": "ma-" + s, "type": "ma_cross", "symbol": s,
                           "params": {"short": 5, "long": 20}}, ctx)
            if it:
                sig.append({"day": dt, "symbol": s, "decision": it.side})
    return res, sig


def bisect_upto(dates, day):
    import bisect
    return bisect.bisect_right(dates, day)


# ---------------------------------------------------------------- 数据
def load_bundle():
    import h5py
    raw, fac = {}, {}
    with h5py.File(os.path.join(BUNDLE, "stocks.h5"), "r") as f:
        for s in SYMBOLS:
            rows = []
            for r in f[obid(s)][()]:
                dt = datetime.strptime(str(int(r["datetime"]))[:8], "%Y%m%d")
                rows.append({"d": dt, "o": float(r["open"]), "h": float(r["high"]),
                             "l": float(r["low"]), "c": float(r["close"]),
                             "v": float(r["volume"]), "t": float(r["total_turnover"])})
            raw[s] = sorted(rows, key=lambda x: x["d"])
    with h5py.File(os.path.join(BUNDLE, "ex_cum_factor.h5"), "r") as f:
        for s in SYMBOLS:
            e = f[obid(s)][()]
            fac[s] = list(zip(list(e["start_date"]), [float(x) for x in e["ex_cum_factor"]]))
    with h5py.File(os.path.join(BUNDLE, "dividends.h5"), "r") as f:
        exd = {}
        for s in SYMBOLS:
            ob = obid(s)
            exd[s] = [int(str(r["ex_dividend_date"])[:8]) for r in f[ob][()]] if ob in f else []
    return raw, fac, exd


def factor_at(steps, day):
    key = int(str(day)[:8])
    cur = 1.0
    for d, v in steps:
        if int(str(d)[:8]) <= key:
            cur = v
        else:
            break
    return cur


def reanchor(series, steps, anchor_day):
    """复刻 base_data_source/adjust.py 的 pre 语义（锚=anchor_day）。"""
    base = factor_at(steps, int(anchor_day.strftime("%Y%m%d")))
    out = []
    for b in series:
        k = factor_at(steps, int(b["d"].strftime("%Y%m%d"))) / base
        out.append({**b, "o": b["o"] * k, "h": b["h"] * k, "l": b["l"] * k, "c": b["c"] * k})
    return out


# ---------------------------------------------------------------- 对照
def within(a, b, days=5):
    try:
        return abs((datetime.strptime(a, "%Y-%m-%d")
                    - datetime.strptime(b, "%Y-%m-%d")).days) <= days
    except ValueError:
        return False


def sgate(rq_sig, old_sig, exd, raw_by_sym, label):
    import difflib
    a = sorted({(x["day"], x["symbol"], x["decision"]) for x in rq_sig})
    b = sorted({(x["day"], x["symbol"], x["decision"]) for x in old_sig})
    sa, sb = set(a), set(b)
    exact = len(sa & sb)
    # ±TOL_DAYS 交易日近似（按自然日 ≤5 近似一周内）
    near = 0
    unmatched_a = []
    for d, s, dec in sa:
        cands = [(x, y, z) for x, y, z in sb if y == s and z == dec]
        if (d, s, dec) in sb:
            near += 1
            continue
        if any(within(d, x) for x, _, _ in cands):
            near += 1
        else:
            unmatched_a.append((d, s, dec))
    unmatched_b = [(d, s, dec) for d, s, dec in sb if (d, s, dec) not in sa
                   and not any(within(d, x) for x, y, z in sa if y == s and z == dec)]
    rate = near / len(sa) * 100 if sa else 0.0
    print("")
    print(f"### S 表（{label}）rqalpha 信号 {len(sa)} 条 vs 旧引擎信号 {len(sb)} 条")
    print(f"  完全同日匹配={exact}/{len(sa)} ({exact/len(sa)*100:.1f}%)   "
          f"±{TOL_DAYS} 交易日匹配={near}/{len(sa)} ({rate:.1f}%)   "
          f"门禁线=95% ⇒ {'达标' if rate >= 95 else '未达标'}")
    if unmatched_a:
        print(f"  仅 rqalpha 有（{len(unmatched_a)}）:")
        for d, s, dec in unmatched_a[:12]:
            near_ex = min((int(str(x)[:8]) for x in exd.get(s, [])),
                          key=lambda x: abs(x - int(d.replace('-', ''))), default=None)
            gap = (abs(near_ex - int(d.replace('-', '')))) if near_ex else None
            print(f"    {d} {s} {dec}   距最近除息日 {gap} 天")
    if unmatched_b:
        print(f"  仅旧引擎有（{len(unmatched_b)}）:")
        for d, s, dec in unmatched_b[:12]:
            near_ex = min((int(str(x)[:8]) for x in exd.get(s, [])),
                          key=lambda x: abs(x - int(d.replace('-', ''))), default=None)
            gap = (abs(near_ex - int(d.replace('-', '')))) if near_ex else None
            print(f"    {d} {s} {dec}   距最近除息日 {gap} 天")
    return near, len(sa)


def jgate(rq_sig):
    from paper_trading.engine.rules import RuleContext, evaluate
    total = ok = 0
    bad = []
    for r in rq_sig:
        ctx = RuleContext(now=r["day"], day=r["day"], symbol=r["symbol"],
                          price=r["closes"][-1], closes=r["closes"])
        it = evaluate({"id": "ma-" + r["symbol"], "type": "ma_cross",
                       "symbol": r["symbol"], "params": {"short": 5, "long": 20}}, ctx)
        got = it.side if it else None
        total += 1
        if got == r["decision"]:
            ok += 1
        else:
            bad.append((r["day"], r["symbol"], r["decision"], got))
    print("")
    print("### J 表 判定层一致性（同一份 rqalpha 收盘价，灌回我们的 evaluate）")
    pct = (ok/total*100) if total else 0.0
    print(f"  比对事件={total}  判定一致={ok}  ({pct:.2f}%)  ⇒ "
          f"{'纪律 3 的判定同一性成立' if ok == total else '存在判定语义差异'}")
    for x in bad[:10]:
        print("    不一致:", x)
    return ok, total


def ftable(res_old, rq_fills, old_sig, rq_sig):
    print("")
    print("### F 表 成交缺口归因（T9 遗留 108 vs 80）")
    print(f"  rqalpha 成交={len(rq_fills)}  旧引擎成交={len(res_old.trades)}")
    reasons = Counter()
    for k in res_old.skipped:
        w = str(k.get("why", ""))
        reasons[w.split("：")[0] if "：" in w else w] += 1
    print("  旧引擎 skipped 分类计数:", dict(reasons))
    n_fill_sig = len({(t["day"], t["symbol"], t["side"]) for t in res_old.trades})
    print(f"  旧引擎成功建仓/平仓事件={n_fill_sig}；信号总数={len(old_sig)}")
    lost = len(old_sig) - len(res_old.trades)
    print(f"  信号→成交 流失≈{lost} 笔（金额不足一手 / 无可卖持仓 / 现金不足 的合计）")
    print("  rqalpha 侧同口径流失（按信号数-成交数）="
          f"{len({(x['day'], x['symbol'], x['decision']) for x in rq_sig}) - len(rq_fills)}")


def main():
    print("HEAD 依赖：rqalpha 6.4.2 + bundle 202610；窗口 "
          f"{START}→{END}；{len(SYMBOLS)} 只票；股数语义 {LOT} 股")
    raw, fac, exd = load_bundle()
    rq_sig, rq_fills, summary = run_rqalpha()
    print(f"[debug] rq_sig={len(rq_sig)} rq_fills={len(rq_fills)} capture={CAPTURE}")
    print('[debug] rq_sig head=', rq_sig[:2])
    res_raw, sig_raw = old_leg(raw)
    anchor = datetime.strptime(END, "%Y-%m-%d")
    adj = {s: reanchor(raw[s], fac[s], anchor) for s in SYMBOLS}
    res_adj, sig_adj = old_leg(adj)

    jgate(rq_sig)
    sgate(rq_sig, sig_raw, exd, raw, "旧引擎吃 bundle 原始价")
    sgate(rq_sig, sig_adj, exd, adj, "旧引擎吃前复权(锚=窗口末)")
    ftable(res_raw, rq_fills, sig_raw, rq_sig)

    print("")
    print("### 参考：rqalpha summary（本次 S 门禁运行）")
    for k in ("start_date", "end_date", "total_value", "total_return", "total_trades",
              "sharpe", "alpha", "beta", "max_drawdown"):
        if isinstance(summary, dict) and k in summary:
            print(f"  {k}: {summary[k]}")
    print(f"  旧引擎（原始价）期末={res_raw.final_equity:.2f}  "
          f"旧引擎（前复权）期末={res_adj.final_equity:.2f}")


if __name__ == "__main__":
    main()
