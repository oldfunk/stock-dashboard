"""T10 (M1-C)：除权日 walkthrough —— 用真实分红票实测 rqalpha 路线 B 的数学自洽。

票：600887.XSHG 伊利股份，bundle dividends 记录 ex_dividend_date=20260605,
    dividend_cash_before_tax=9.00 / round_lot=10 ⇒ 每股税前现金分红 0.90 元。
做法：除权日前买入 1000 股并持有穿越除权日，逐日打印
      原始 close / prev_close(=交易所除息参考价) / 现金 / 持仓市值 /
      应收分红 dividend_receivable / 账户总值。
判据：除权日账户总值变化应 ≈ 价格自身波动，**不应出现 ≈ DPS×股数 的假跌**。

只读：不落库、不下真实单，数据全来自 rqalpha bundle。
"""
import os

from rqalpha import run_func

BUNDLE = os.path.expanduser(r"~\.rqalpha\bundle")
OB = "600887.XSHG"
BUY_DAY = "2026-06-02"          # 除权日前一个交易日
EX_DAY = "2026-06-05"
QTY = 1000
DPS = 0.90

rows = []


def init(context):
    context.bought = False


def handle_bar(context, bar_dict):
    from rqalpha.api import order_shares
    from rqalpha.const import POSITION_DIRECTION
    d = context.now.strftime("%Y-%m-%d")
    if d == BUY_DAY and not context.bought:
        order_shares(OB, QTY)
        context.bought = True
    if BUY_DAY <= d <= "2026-06-12":
        pos = context.portfolio.get_position(OB, POSITION_DIRECTION.LONG)
        b = bar_dict[OB]
        rows.append({
            "day": d,
            "close": round(float(getattr(b, "close", 0.0) or 0.0), 3),
            "prev_close": round(float(getattr(b, "prev_close", 0.0) or 0.0), 3),
            "qty": int(getattr(pos, "quantity", 0) or 0),
            "cash": round(float(context.portfolio.cash), 2),
            "pos_equity": round(float(getattr(pos, "equity", 0.0) or 0.0), 2),
            "div_recv": round(float(getattr(pos, "dividend_receivable", 0.0) or 0.0), 2),
            "total": round(float(context.portfolio.total_value), 2),
        })


def main():
    cfg = {"base": {"start_date": "2026-05-20", "end_date": "2026-06-12",
                    "accounts": {"STOCK": 100_000.0}, "frequency": "1d",
                    "data_bundle_path": BUNDLE, "capital_gain_tax_rate": 0.0,
                    "locale": "zh_CN"},
           "mod": {"sys_analyser": {"benchmark": "000300.XSHG"},
                   "sys_simulation": {"slippage": 0},
                   "sys_transaction_cost": {"stock_commission_multiplier": 0.00025 / 0.0008,
                                            "stock_min_commission": 5.0}}}
    run_func(init=init, handle_bar=handle_bar, config=cfg)

    print("day         close  prev_close   qty      cash  pos_equity  div_recv     total")
    for r in rows:
        print(f"{r['day']}  {r['close']:>7.3f}  {r['prev_close']:>9.3f} {r['qty']:>5} "
              f"{r['cash']:>10.2f} {r['pos_equity']:>10.2f} {r['div_recv']:>9.2f} {r['total']:>10.2f}")

    ex = [r for r in rows if r["day"] == EX_DAY]
    pre = [r for r in rows if r["day"] == "2026-06-04"]
    if ex and pre:
        e, p = ex[0], pre[0]
        expect_ref = round(p["close"] - DPS, 3)
        print("")
        print("### 判据")
        print(f"  交易所除息参考价应为 close(06-04) − DPS = {p['close']} − {DPS} = {expect_ref}"
              f"；rqalpha 看到的 prev_close(06-05) = {e['prev_close']}")
        print(f"  持仓市值变化 = {e['pos_equity']} − {p['pos_equity']} = {e['pos_equity']-p['pos_equity']:+.2f}"
              f"（纯除息应有 −{DPS*QTY:.2f} 再叠加当日价格波动）")
        print(f"  应收分红变化 = {e['div_recv']} − {p['div_recv']} = {e['div_recv']-p['div_recv']:+.2f}")
        print(f"  现金变化     = {e['cash']} − {p['cash']} = {e['cash']-p['cash']:+.2f}")
        print(f"  **账户总值变化 = {e['total']} − {p['total']} = {e['total']-p['total']:+.2f}**"
              f"（路线 B 自洽 ⇔ 不出现 ≈ −{DPS*QTY:.2f} 的假跌）")
        # 找出现金/应收变化的那一天（rqalpha 入账时点：登记日/除权日/派息日？）
        for tag in ("div_recv", "cash"):
            jumps = [(r["day"], round(r[tag], 2)) for r in rows]
            print(f"  {tag} 逐日: {jumps}")


if __name__ == "__main__":
    main()
