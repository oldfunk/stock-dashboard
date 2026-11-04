"""T9 (M1-B)：旧 Backtester vs rqalpha —— 同规则 / 同窗口 / 同数据 的 parity 对照。

分层理由：§5.1-6 的容差只有在**数据完全相同**时才能把差异判给"引擎语义"。

  L1   旧引擎吃 bundle **原始价**（滑点 0）
  L1b  rqalpha 原生 bundle 同窗口（滑点 0、佣金乘数对齐 0.00025）→ 差异归因引擎语义
  L2   旧引擎吃"前复权(锚=窗口末)"（= 现网 qfq 口径）→ 量化复权锚点漂移
  L3   费用模型逐项对照（我们 0.00025+过户费 vs rqalpha 默认 0.0008、无过户费）
  L4   撮合时点固有价差分布（我们 T+1 开盘 vs rqalpha 当日收盘）

只读：不落任何库、不下单、不碰生产数据；数据全部来自 rqalpha bundle（真实市场数据）。
"""
import difflib
import h5py
import os
import statistics
import sys
from datetime import datetime

BUNDLE = os.path.expanduser(r"~\.rqalpha\bundle")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from paper_trading.engine.backtest import Backtester, FeeModel        # noqa: E402
from paper_trading.models import Bar                                  # noqa: E402

SYMBOLS = ["000858", "601318", "600036", "601398", "600900",
           "000333", "600887", "600276", "601888", "000651"]
START, END = "2026-01-05", "2026-09-10"
WARMUP_FROM = "2025-09-01"
INIT_CASH = 100_000.0
LOT = 100
#: 旧引擎按"金额"下单（size_for_value 向下取整到整手），rqalpha 按"股数"直下。
#: 用 100×窗口首价时，价格一旦上穿就买 0 股（信号静默消失）→ 用本系数放大以做归因实验。
SIZE_MULT = float(os.environ.get("SIZE_MULT", "100"))


def d2date(v):
    return datetime.strptime(str(int(v))[:8], "%Y%m%d")


def obid(sym):
    return f"{sym}.{'XSHG' if sym[0] in '69' else 'XSHE'}"


def code_of(ob):
    return str(ob).split(".")[0]


def factor_at(steps, day):
    """bundle 的 ex_cum_factor.start_date 带 000000 时间分量 ⇒ 两侧都归一到 YYYYMMDD。"""
    key = int(str(day)[:8])
    cur = 1.0
    for d, v in steps:
        if int(str(d)[:8]) <= key:
            cur = v
        else:
            break
    return cur


def load_bars():
    raw, fac = {}, {}
    with h5py.File(os.path.join(BUNDLE, "stocks.h5"), "r") as f:
        for s in SYMBOLS:
            rows = []
            for r in f[obid(s)][()]:
                dt = d2date(r["datetime"])
                ds = dt.strftime("%Y-%m-%d")
                if WARMUP_FROM <= ds <= END:
                    rows.append({"date": dt, "open": float(r["open"]),
                                 "high": float(r["high"]), "low": float(r["low"]),
                                 "close": float(r["close"]),
                                 "prev_close": float(r["prev_close"]),
                                 "limit_up": float(r["limit_up"]),
                                 "limit_down": float(r["limit_down"]),
                                 "volume": float(r["volume"]),
                                 "turnover": float(r["total_turnover"])})
            raw[s] = sorted(rows, key=lambda x: x["date"])
    with h5py.File(os.path.join(BUNDLE, "ex_cum_factor.h5"), "r") as f:
        for s in SYMBOLS:
            e = f[obid(s)][()]
            fac[s] = list(zip(list(e["start_date"]),
                              [float(x) for x in e["ex_cum_factor"]]))
    return raw, fac


def adjusted(series, steps, anchor):
    """base_data_source/adjust.py 的 pre 语义：price * f(t) / f(anchor)。"""
    base = factor_at(steps, int(anchor.strftime("%Y%m%d")))
    out = []
    for b in series:
        k = factor_at(steps, int(b["date"].strftime("%Y%m%d"))) / base
        out.append({**b, "open": b["open"] * k, "high": b["high"] * k,
                    "low": b["low"] * k, "close": b["close"] * k})
    return out


def build(series_by_sym):
    bm, days = {}, set()
    for s, ser in series_by_sym.items():
        bm[s] = [Bar(symbol=s, timestamp=b["date"], open=round(b["open"], 2),
                     high=round(b["high"], 2), low=round(b["low"], 2),
                     close=round(b["close"], 2), volume=int(b["volume"]),
                     turn=b["turnover"]) for b in ser]
        days |= {b["date"].strftime("%Y-%m-%d") for b in ser
                 if START <= b["date"].strftime("%Y-%m-%d") <= END}
    return bm, sorted(days)


def make_cfg(series_by_sym):
    rules = []
    for s, ser in series_by_sym.items():
        win = [b for b in ser if START <= b["date"].strftime("%Y-%m-%d") <= END]
        p0 = win[0]["close"] if win else 100.0
        rules.append({"id": "ma-" + s, "type": "ma_cross", "symbol": s,
                      "params": {"short": 5, "long": 20},
                      "action": {"side": "buy", "value": round(SIZE_MULT * p0, 2)},
                      "once": True})
    return {"rules": rules, "min_ticket_cny": 0, "max_orders_per_day": 0,
            "max_turnover_per_day": 0}


def run_old(series_by_sym, tag):
    bm, days = build(series_by_sym)
    bt = Backtester(make_cfg(series_by_sym), fee=FeeModel(),
                    slippage_pct=0.0, slippage_fixed=0.0)
    res = bt.run(bm, days, initial_cash=INIT_CASH)
    tr = res.trades
    fees = sum(t["commission"] + t.get("stamp_duty", 0.0) + t.get("transfer_fee", 0.0)
               for t in tr)
    nb = sum(1 for t in tr if t["side"] == "buy")
    print("")
    print("### " + tag)
    print(f"  trades={len(tr)} (buy={nb} sell={len(tr)-nb})  fees={fees:.4f}  "
          f"final={res.final_equity:.2f}  ret={res.final_equity/INIT_CASH*100-100:+.3f}%")
    print("  head=" + str([(t["day"], t["symbol"], t["side"], t["volume"], t["price"])
                           for t in tr[:5]]))
    return {"n": len(tr), "fees": fees, "eq": res.final_equity,
            "sig": sorted((t["day"], t["symbol"], t["side"]) for t in tr),
            "rows": [{"day": t["day"], "symbol": t["symbol"], "side": t["side"],
                      "volume": float(t["volume"]), "price": float(t["price"]),
                      "fee": t["commission"] + t.get("stamp_duty", 0.0)
                            + t.get("transfer_fee", 0.0)} for t in tr]}


def run_rqalpha():
    try:
        from rqalpha import run_func
    except Exception as e:                                    # noqa: BLE001
        print("rqalpha 不可用:", e)
        return None
    syms = [obid(s) for s in SYMBOLS]

    def init(context):
        context.symbols = syms

    def handle_bar(context, bar_dict):
        from rqalpha.api import history_bars, order_shares
        from rqalpha.const import POSITION_DIRECTION
        for ob in context.symbols:
            try:
                closes = history_bars(ob, 21, "1d", "close")
            except Exception:                                 # noqa: BLE001
                continue
            if closes is None or len(closes) < 21:
                continue
            c = [float(x) for x in closes]
            ma5, ma20 = sum(c[-5:]) / 5, sum(c[-20:]) / 20
            ma5_p, ma20_p = sum(c[-6:-1]) / 5, sum(c[-21:-1]) / 20
            golden = ma5_p <= ma20_p and ma5 > ma20
            death = ma5_p >= ma20_p and ma5 < ma20
            pos = context.portfolio.get_position(ob, POSITION_DIRECTION.LONG)
            qty = int(pos.quantity) if pos else 0
            if golden and qty == 0:
                order_shares(ob, LOT)
            elif death and qty > 0:
                order_shares(ob, -min(LOT, qty))

    cfg = {
        "base": {"start_date": START, "end_date": END,
                 "accounts": {"STOCK": INIT_CASH}, "frequency": "1d",
                 "data_bundle_path": BUNDLE, "capital_gain_tax_rate": 0.0,
                 "locale": "zh_CN"},
        "mod": {"sys_analyser": {"benchmark": "000300.XSHG"},
                "sys_simulation": {"slippage": 0},
                "sys_transaction_cost": {
                    "stock_commission_multiplier": 0.00025 / 0.0008,
                    "stock_min_commission": 5.0, "tax_multiplier": 1.0}},
    }
    try:
        result = run_func(init=init, handle_bar=handle_bar, config=cfg)
    except Exception as e:                                    # noqa: BLE001
        print("rqalpha 运行失败:", type(e).__name__, e)
        return None

    sa = result.get("sys_analyser", {}) if isinstance(result, dict) else {}
    summary = sa.get("summary") or {}
    trades = sa.get("trades")
    raw_rows = []
    if trades is not None:
        try:
            raw_rows = [dict(r) for _, r in trades.iterrows()]
        except Exception:                                     # noqa: BLE001
            raw_rows = []

    def num(x):
        try:
            return float(x)
        except Exception:                                     # noqa: BLE001
            return 0.0

    norm = []
    for r in raw_rows:
        side = str(r.get("side", "")).lower()
        norm.append({"day": str(r.get("datetime") or r.get("date"))[:10],
                     "symbol": code_of(r.get("order_book_id") or r.get("symbol")),
                     "side": "buy" if "buy" in side else "sell",
                     "volume": num(r.get("quantity") or r.get("last_quantity")),
                     "price": num(r.get("price") or r.get("last_price")),
                     "fee": num(r.get("transaction_cost"))})
    fees = sum(x["fee"] for x in norm)
    eq = num(summary.get("total_value"))
    nb = sum(1 for x in norm if x["side"] == "buy")
    print("")
    print("### L1b rqalpha 原生 bundle（滑点 0，stock_commission_multiplier=0.3125）")
    print(f"  trades={len(norm)} (buy={nb} sell={len(norm)-nb})  fees={fees:.4f}  "
          f"final={eq:.2f}  ret={eq/INIT_CASH*100-100:+.3f}%")
    print("  head=" + str([(x["day"], x["symbol"], x["side"], x["volume"], x["price"])
                           for x in norm[:5]]))
    return {"n": len(norm), "fees": fees, "eq": eq,
            "sig": sorted((x["day"], x["symbol"], x["side"]) for x in norm),
            "rows": norm}


def compare(old, rq):
    print("")
    print("### 成交集合对齐（day|symbol|side；rqalpha 当日收盘 vs 我们 T+1 开盘）")
    a = [f"{d}|{s}|{sd}" for d, s, sd in old["sig"]]
    b = [f"{d}|{s}|{sd}" for d, s, sd in rq["sig"]]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    same = sum(bl.size for bl in sm.get_matching_blocks())
    print(f"  旧={len(a)}  rq={len(b)}  同日同票同向匹配={same}  "
          f"旧独有={len(a)-same}  rq独有={len(b)-same}")
    seta, setb = set(a), set(b)
    for x in sorted(seta - setb)[:8]:
        print("    旧引擎独有: " + x)
    for x in sorted(setb - seta)[:8]:
        print("    rqalpha 独有: " + x)

    print("")
    print("### 逐笔配对（同 symbol+side，取最近日期）后的价格/股数差异")
    pairs, used = [], set()
    for o in old["rows"]:
        best, bd, bj = None, 99, None
        for j, r in enumerate(rq["rows"]):
            if j in used or r["symbol"] != o["symbol"] or r["side"] != o["side"]:
                continue
            try:
                dd = abs((datetime.strptime(r["day"], "%Y-%m-%d")
                          - datetime.strptime(o["day"], "%Y-%m-%d")).days)
            except ValueError:
                continue
            if dd < bd:
                best, bd, bj = r, dd, j
        if best is not None and bd <= 7:
            used.add(bj)
            pairs.append((o, best, bd))
    if pairs:
        pd = [abs(x["price"] - y["price"]) / x["price"] * 100 for x, y, _ in pairs if x["price"]]
        qd = sum(1 for x, y, _ in pairs if abs(x["volume"] - y["volume"]) > 0.5)
        lags = [d for _, _, d in pairs]
        print(f"  可配对={len(pairs)}/{len(old['rows'])}  日期差 mean={statistics.mean(lags):.2f} 天  "
              f"价格相对差 mean={statistics.mean(pd):.3f}%  max={max(pd):.3f}%  股数不同={qd} 笔")
    print(f"  费用合计：旧={old['fees']:.4f}  rq={rq['fees']:.4f}  差={old['fees']-rq['fees']:+.4f}")
    if rq["eq"]:
        print(f"  期末权益：旧={old['eq']:.2f}  rq={rq['eq']:.2f}  "
              f"差={old['eq']-rq['eq']:+.2f} ({(old['eq']/rq['eq']-1)*100:+.3f}%)")


def main():
    raw, fac = load_bars()
    anchor = datetime.strptime(END, "%Y-%m-%d")

    r1 = run_old(raw, "L1 旧引擎 / bundle 原始价 / 无滑点")
    adj = {s: adjusted(raw[s], fac[s], anchor) for s in SYMBOLS}
    r2 = run_old(adj, "L2 旧引擎 / 前复权(锚=窗口末，模拟现网 qfq 口径)")

    print("")
    print("### 复权锚点漂移（L1 vs L2 成交集合）")
    s1 = {(d, s, sd) for d, s, sd in r1["sig"]}
    s2 = {(d, s, sd) for d, s, sd in r2["sig"]}
    print(f"  仅原始价={len(s1-s2)}  仅前复权={len(s2-s1)}  共同={len(s1&s2)}")
    print(f"  期末权益：原始价={r1['eq']:.2f}  前复权={r2['eq']:.2f} 差={r1['eq']-r2['eq']:+.2f}")
    for x in sorted(s1 - s2)[:5]:
        print("    raw-only " + str(x))
    for x in sorted(s2 - s1)[:5]:
        print("    qfq-only " + str(x))

    rq = run_rqalpha()
    if rq:
        compare(r1, rq)

    print("")
    print("### L3 费用模型逐项（100 股 × 100.00 = 10,000 元卖出）")
    c, st, t = FeeModel().sell_cost(10_000.0)
    print(f"  我们    : commission={c:.4f} stamp={st:.4f} transfer={t:.4f} 合计={c+st+t:.4f}")
    rq_c = max(10_000.0 * 0.0008, 5.0)
    print(f"  rqalpha : commission(默认万8)={rq_c:.4f} stamp={10_000*0.0005:.4f} "
          f"过户费=无此项 合计={rq_c+5.0:.4f}")
    print(f"  对齐：stock_commission_multiplier={0.00025/0.0008:.4f}；"
          f"过户费 0.001% 双向 需自定义 decider")

    print("")
    print("### L4 撮合时点固有价差（我们 T+1 开盘 vs rqalpha 当日收盘）")
    slips = []
    for s in SYMBOLS:
        ser = raw[s]
        for i in range(len(ser) - 1):
            if START <= ser[i]["date"].strftime("%Y-%m-%d") <= END:
                slips.append(abs(ser[i + 1]["open"] / ser[i]["close"] - 1.0))
    if slips:
        p90 = sorted(slips)[int(len(slips) * 0.9)]
        print(f"  n={len(slips)}  mean={statistics.mean(slips)*100:.3f}%  "
              f"median={statistics.median(slips)*100:.3f}%  p90={p90*100:.3f}%  "
              f"max={max(slips)*100:.3f}%")
        print("  ⇒ 撮合时点不同 ⇒ '逐笔价格差 <1e-6' 在日线频率下不可达；"
              "rqalpha 日线强制 next_bar→current_bar（sys_simulation/mod.py:59-62）")


if __name__ == "__main__":
    main()
