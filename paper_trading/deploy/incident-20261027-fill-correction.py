"""事故冲正脚本存档：fill #11 (000792 @175.97) 按计划基准价 22.25 重记。

⚠️ **审计存档，禁止直接重跑**（首步幂等断言会挡住第二次执行；如需再做
   账本冲正，必须按新事故另写脚本并走用户确认）。存档目的（R6.4）：
   记录 2026-10-27 实际执行的完整过程——改前备份、逐步断言、op_log 留痕。

背景：v0.7.1/v0.7.2 执行侧 price_map 取母库 close_adj（后复权 175.96），
v0.7.3 已修（切 close_qfq 前复权）。此脚本冲正污染账本，全程记 op_log
data:incident，改前先备份 paper_account.db。
"""
import json
import shutil
import sqlite3
from datetime import datetime

DB = "paper_account.db"
FILL_ID, ORDER_ID = 11, 12
SYM = "000792"
OLD_PRICE, NEW_PRICE = 175.97, 22.25
NAV_BAD_ID = 13          # 09:32 污染快照（total=155999）
CANCEL_COND = 29         # 000792 price_above 180（假价格止盈）

backup = DB + ".bak-incident-20261027"
shutil.copy2(DB, backup)
print("backup ->", backup)

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
now = datetime.now().isoformat(timespec="seconds")

fill = dict(c.execute("SELECT * FROM fills WHERE fill_id=?", (FILL_ID,)).fetchone())
order = dict(c.execute("SELECT * FROM orders WHERE order_id=?", (ORDER_ID,)).fetchone())
pos = dict(c.execute("SELECT * FROM positions WHERE symbol=?", (SYM,)).fetchone())
acct = dict(c.execute("SELECT * FROM account WHERE id=1").fetchone())
nav_bad = dict(c.execute("SELECT * FROM nav_history WHERE id=?", (NAV_BAD_ID,)).fetchone())

assert fill["price"] == OLD_PRICE, f"fill price {fill['price']} != {OLD_PRICE}，停手"
assert nav_bad["total_value"] > 150000, "NAV #13 不像污染行，停手"

vol = fill["volume"]
new_amount = NEW_PRICE * vol
new_comm = max(new_amount * 0.00025, 5.0)
new_transfer = new_amount * 0.0001
new_total = new_amount + new_comm + new_transfer
old_total = OLD_PRICE * vol + fill["commission"] + fill["stamp_duty"] + fill["transfer_fee"]
refund = round(old_total - new_total, 4)
new_cash = round(acct["cash"] + refund, 4)
new_avg_cost = round(new_total / vol, 6)

incident = {
    "date": "2026-10-27",
    "kind": "price_scale_pollution",
    "root_cause": "v0.7.1/v0.7.2 执行侧 price_map 取母库 close_adj（后复权）",
    "fix": "v0.7.3 切 close_qfq；本脚本按计划基准价 22.25 冲正（用户确认）",
    "fill_id": FILL_ID, "order_id": ORDER_ID, "symbol": SYM,
    "before": {"fill_price": OLD_PRICE, "order_limit": order["limit_price"],
               "avg_cost": pos["avg_cost"], "peak_price": pos["peak_price"],
               "cash": acct["cash"], "nav13_total": nav_bad["total_value"]},
    "after": {"fill_price": NEW_PRICE, "avg_cost": new_avg_cost,
              "peak_price": new_avg_cost, "cash": new_cash,
              "nav13": "deleted (polluted snapshot)"},
    "refund": refund,
}

with c:
    c.execute("UPDATE fills SET price=?, commission=?, stamp_duty=?, transfer_fee=? WHERE fill_id=?",
              (NEW_PRICE, new_comm, 0.0, new_transfer, FILL_ID))
    c.execute("""UPDATE orders SET limit_price=?, filled_price=?, commission=?,
                stamp_duty=?, transfer_fee=? WHERE order_id=?""",
              (NEW_PRICE, NEW_PRICE, new_comm, 0.0, new_transfer, ORDER_ID))
    c.execute("""UPDATE positions SET avg_cost=?, peak_price=?, last_update=?
                WHERE symbol=?""",
              (new_avg_cost, new_avg_cost, now, SYM))
    c.execute("UPDATE account SET cash=? WHERE id=1", (new_cash,))
    c.execute("DELETE FROM nav_history WHERE id=?", (NAV_BAD_ID,))
    c.execute("UPDATE conditional_orders SET status='cancelled' WHERE id=? AND status='armed'",
              (CANCEL_COND,))
    # 审计：冲正前后全量 + 污染 NAV 行留档在 incident JSON 里
    incident["nav13_deleted_row"] = {k: nav_bad[k] for k in nav_bad}

    # 用本地前复权最新收盘真实重算持仓市值（审计里的 total_value_after 要给准数）
    loc = sqlite3.connect("file:data.db?mode=ro", uri=True)
    mv, mv_detail = 0.0, {}
    for p in c.execute("SELECT symbol, total_volume FROM positions"):
        r = loc.execute("""SELECT close FROM daily_bars WHERE symbol=?
                           ORDER BY timestamp DESC LIMIT 1""",
                        (p["symbol"],)).fetchone()
        if r:
            mv += r[0] * p["total_volume"]
            mv_detail[p["symbol"]] = round(r[0] * p["total_volume"], 2)
    loc.close()
    real_total = round(new_cash + mv, 2)
    incident["real_nav_recompute"] = {"market_value": round(mv, 2),
                                      "by_symbol": mv_detail,
                                      "total_value": real_total,
                                      "note": "nav_history 污染行已删，收盘 tick 会重记 10-27 正确行"}

    c.execute("""INSERT INTO op_log (timestamp, action, params, ok, result,
                cash_after, total_value_after) VALUES (?, 'data:incident', ?, 1, ?, ?, ?)""",
              (now, json.dumps({"fill_id": FILL_ID, "symbol": SYM}, ensure_ascii=False),
               json.dumps(incident, ensure_ascii=False), new_cash, real_total))

c.close()

# 复核
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
print("fill:", dict(c.execute("SELECT price, commission, transfer_fee FROM fills WHERE fill_id=?", (FILL_ID,)).fetchone()))
print("order:", dict(c.execute("SELECT limit_price, filled_price FROM orders WHERE order_id=?", (ORDER_ID,)).fetchone()))
print("pos:", dict(c.execute("SELECT avg_cost, peak_price FROM positions WHERE symbol=?", (SYM,)).fetchone()))
print("cash:", c.execute("SELECT cash FROM account WHERE id=1").fetchone()[0])
print("nav rows:", c.execute("SELECT COUNT(*) FROM nav_history").fetchone()[0], "(应少 1)")
print("cond 29:", c.execute("SELECT status FROM conditional_orders WHERE id=?", (CANCEL_COND,)).fetchone()[0])
print("incident log:", c.execute("SELECT COUNT(*) FROM op_log WHERE action='data:incident'").fetchone()[0])
c.close()
print("DONE")
