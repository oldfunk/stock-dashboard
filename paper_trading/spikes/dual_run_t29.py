"""T29 · M1 签字最后拼图：T16 钱类场景的 **新引擎（rqalpha）侧**双跑。

对应用例（tests/test_compliance_regression.py 里的 6 条 + 其 TODO 对照位）：
  R1  T16-1 事故日尺度重放（000792 真实价 23.5 / 涨停 25.44，事故价 175.96 = 7.5×）
      → 期望：限价单被拒、**零成交**
  R2  涨跌停带（市价单在涨停收盘日）→ 期望零成交
  R3  费用口径（佣金地板 / 印花税卖出单边 / 过户费）→ 与我们的 FeeModel 逐项对差

每个 run 都带 **空转断言**（handler_invocations>0 且对照组有成交）——
T19 踩过 "harness 静默 0 事件但退出码 0" 的坑。

只读：数据全来自 bundle，不落任何库、不碰生产。
"""
import json
import os
import sys

import h5py
import numpy as np

BUNDLE = os.path.expanduser(r"~\.rqalpha\bundle")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from rqalpha import run_func                                   # noqa: E402

INCIDENT_OB = "000792.XSHE"        # 事故股：真实价 23.5 左右
INCIDENT_POLLUTED = 175.96         # 10-27 事故里被当成交价的母库后复权价
RESULT = {}


def _bars(ob):
    with h5py.File(os.path.join(BUNDLE, "stocks.h5"), "r") as f:
        a = f[ob][()]
    return a


def _run(tag, ob, start, end, submit, extra_mod=None, once=True):
    """跑一次 rqalpha，返回 {trades, handler_calls, control_fills}。"""
    st = {"calls": 0, "orders": []}

    def init(context):
        context.ob = ob
        context.done = False

    def handle_bar(context, bar_dict):
        from rqalpha.api import order, order_shares
        st["calls"] += 1
        day = context.now.strftime("%Y-%m-%d")
        b = bar_dict[context.ob]
        if not st["orders"]:
            st["orders"] = [{"day": day, "close": float(b.close),
                             "limit_up": float(b.limit_up),
                             "prev_close": float(b.prev_close)}]
        if once and len(st["orders"]) > 1:
            return
        submit(order, order_shares, context, b)
        st["orders"].append({"day": day})

    cfg = {"base": {"start_date": start, "end_date": end,
                    "accounts": {"STOCK": 1_000_000.0}, "frequency": "1d",
                    "data_bundle_path": BUNDLE, "capital_gain_tax_rate": 0.0,
                    "locale": "zh_CN"},
           "mod": {"sys_analyser": {"benchmark": "000300.XSHG"},
                   "sys_simulation": {"slippage": 0}}}
    if extra_mod:
        cfg["mod"].update(extra_mod)
    res = run_func(init=init, handle_bar=handle_bar, config=cfg)
    sa = res.get("sys_analyser", {}) if isinstance(res, dict) else {}
    tr = sa.get("trades")
    rows = [dict(r) for _, r in tr.iterrows()] if tr is not None and len(tr) else []
    if rows and not RESULT.get("_cols"):
        RESULT["_cols"] = sorted(rows[0].keys())
    summary = sa.get("summary") or {}
    out = {"tag": tag, "handler_calls": st["calls"], "bar": st["orders"][:1],
           "n_fills": len(rows), "rows": rows,
           "total_value": float(summary.get("total_value") or 0.0),
           "fees": float(sum(float(r.get("transaction_cost") or 0) for r in rows))}
    # 空转断言：handler 必须真被调用过（T19 的 numpy 静默坑就长这样）
    assert out["handler_calls"] > 0, f"{tag}: handler 从未被调用 ⇒ 空转，结果不可信"
    return out


def find_limit_up_day(min_dt=20240101, max_scan=600):
    """在 bundle 里找一个真实的"涨停收盘日"（close == limit_up）。"""
    with h5py.File(os.path.join(BUNDLE, "stocks.h5"), "r") as f:
        for i, k in enumerate(list(f.keys())[:max_scan]):
            if i > max_scan:
                break
            a = f[k][()]
            m = np.where((a["datetime"] >= min_dt * 10 ** 6)
                         & (a["close"] == a["limit_up"])
                         & (a["low"] == a["high"]))[0]
            if len(m):
                r = a[m[0]]
                return k, str(int(r["datetime"]))[:8], float(r["close"]), \
                    float(r["prev_close"])
    return None


def main():
    # ---------------- R1 事故日尺度重放：限价单 175.96 超涨停 ⇒ 必须零成交 -------
    def submit_limit_polluted(order, order_shares, ctx, b):
        order(ctx.ob, 100, INCIDENT_POLLUTED)          # 事故价的限价买单

    def submit_market_ok(order, order_shares, ctx, b):
        order_shares(ctx.ob, 100)                     # 对照组：真实价市价单，必须成交

    a = _bars(INCIDENT_OB)
    last = str(int(a[-1]["datetime"]))[:8]
    start = str(int(a[-6]["datetime"]))[:8]
    s1 = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e1 = f"{last[:4]}-{last[4:6]}-{last[6:]}"
    r_poll = _run("R1 事故价限价单", INCIDENT_OB, s1, e1, submit_limit_polluted)
    r_ctrl = _run("R1 对照：真实价市价单", INCIDENT_OB, s1, e1, submit_market_ok)
    assert r_ctrl["n_fills"] > 0, "对照组没成交 ⇒ harness 失效，R1 结论不可用"
    RESULT["R1"] = {"polluted_fills": r_poll["n_fills"],
                    "control_fills": r_ctrl["n_fills"],
                    "bar": r_poll["bar"], "control_rows": r_ctrl["rows"][:2]}

    # ---------------- R2 涨停收盘日的市价买单 ⇒ 必须零成交 ----------------------
    hit = find_limit_up_day()
    if hit:
        ob2, d2, c2, pc2 = hit
        dd = f"{d2[:4]}-{d2[4:6]}-{d2[6:]}"
        import datetime as _dt
        prev = (_dt.datetime.strptime(d2, "%Y%m%d") - _dt.timedelta(days=6)).strftime("%Y-%m-%d")

        def submit_mkt(order, order_shares, ctx, b):
            order_shares(ctx.ob, 100)

        r2 = _run("R2 涨停日市价买单", ob2, prev, dd, submit_mkt)
        RESULT["R2"] = {"instrument": ob2, "day": dd, "close": c2, "prev_close": pc2,
                        "fills": r2["n_fills"], "handler_calls": r2["handler_calls"]}
    else:
        RESULT["R2"] = {"instrument": None, "note": "未在扫描范围内找到一字涨停样本"}

    # ---------------- R3 费用口径：与 FeeModel 逐项对差 --------------------------
    def submit_buy_sell(order, order_shares, ctx, b):
        from rqalpha.const import POSITION_DIRECTION
        pos = ctx.portfolio.get_position(ctx.ob, POSITION_DIRECTION.LONG)
        qty = int(pos.quantity) if pos else 0
        if qty == 0 and not getattr(ctx, "bought", False):
            order_shares(ctx.ob, 8000)
            ctx.bought = True
        elif qty > 0 and getattr(ctx, "bought", False) and not getattr(ctx, "sold", False):
            order_shares(ctx.ob, -qty)
            ctx.sold = True

    r3a = _run("R3 rqalpha 默认费率", INCIDENT_OB, s1, e1, submit_buy_sell, once=False)
    r3b = _run("R3 对齐乘数 0.3125", INCIDENT_OB, s1, e1, submit_buy_sell,
               extra_mod={"sys_transaction_cost": {
                   "stock_commission_multiplier": 0.00025 / 0.0008,
                   "stock_min_commission": 5.0}}, once=False)
    from paper_trading.engine.backtest import FeeModel

    fm = FeeModel()
    legs = []
    for r in (r3a, r3b):
        for row in r["rows"]:
            price = float(row.get("price") or row.get("last_price") or 0)
            qty = abs(float(row.get("quantity") or row.get("last_quantity") or 0))
            side = str(row.get("side", "")).lower()
            amt = price * qty
            if "buy" in side:
                c, tr = fm.buy_cost(amt)
                ours = c + tr
            else:
                c, st, tr = fm.sell_cost(amt)
                ours = c + st + tr
            legs.append({"run": r["tag"], "day": str(row.get("datetime"))[:10],
                         "side": "buy" if "buy" in side else "sell", "qty": qty,
                         "price": round(price, 2), "amount": round(amt, 2),
                         "rq_fee": round(float(row.get("transaction_cost") or 0), 4),
                         "ours_fee": round(ours, 4),
                         "diff": round(float(row.get("transaction_cost") or 0) - ours, 4)})
    RESULT["R3"] = {"legs": legs,
                    "rq_total_default": round(r3a["fees"], 4),
                    "rq_total_aligned": round(r3b["fees"], 4),
                    "ours_total": round(sum(l["ours_fee"] for l in legs), 4)}

    print(json.dumps(RESULT, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
