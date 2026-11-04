"""T46 冒烟：`m3 verify` 的真实 CLI 走一遍（scratch 库，退出码与输出都要看）。"""
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WORK = Path(r"C:\Users\yanzh\AppData\Local\Temp\t46-smoke")
WORK.mkdir(parents=True, exist_ok=True)
for f in WORK.glob("*.db"):
    f.unlink()

sys.path.insert(0, r"C:\Users\yanzh\AppData\Local\Temp\pt-worker-t46")
from paper_trading.broker.paper_broker import PaperBroker      # noqa: E402
from paper_trading.data.db_manager import DataDBManager        # noqa: E402
from paper_trading.models import Bar, TradingConfig            # noqa: E402

TODAY = "2026-10-29"
acct = WORK / "acct.db"
data = WORK / "data.db"
b = PaperBroker(db_path=str(acct), config=TradingConfig())
c = sqlite3.connect(str(acct))
c.execute("""INSERT INTO fills (order_id, symbol, direction, volume, price, commission,
             stamp_duty, transfer_fee, timestamp) VALUES (1,'000651',1,2000,37.58,?,?,?,?)""",
          (2000 * 37.58 * 0.00025, 0.0, 2000 * 37.58 * 0.00001, f"{TODAY}T15:00:00"))
c.execute("""INSERT INTO nav_history (timestamp, cash, market_value, total_value,
             available_cash, pnl, pnl_pct) VALUES (?,?,?,?,?,?,?)""",
          (f"{TODAY}T15:30:00", 92480.4584, 75160.0, 92480.4584 + 75160.0,
           92480.4584, 0.0, 0.0))
c.execute("""INSERT INTO positions (symbol,total_volume,available_volume,avg_cost,
             last_update,entry_day,peak_price) VALUES ('000651',2000,0,37.58,?, ?,0)""",
          (f"{TODAY}T15:00:00", TODAY))
c.execute("""INSERT INTO op_log (timestamp,action,params,ok,result,cash_after,total_value_after)
             VALUES (?,'engine:buy','{}',1,'{}',NULL,NULL)""", (f"{TODAY}T15:00:01",))
c.execute("""INSERT INTO agent_plans (plan_date,symbols,plan_json,status,created_at)
             VALUES (?,?,?,?,?)""",
          (TODAY, '["000651"]', "{}", "executed", f"{TODAY}T16:35:00"))
c.commit()
c.close()

db = DataDBManager(str(data))
bars = [Bar(symbol="000651", timestamp=datetime(2026, 10, d, 15, 0), open=37.5, high=37.7,
             low=37.4, close=37.58, volume=1000, amount=37580.0, adjust="raw")
        for d in (27, 28)]
db.upsert_raw_bars(bars, source="t46-smoke", checked_dates={"2026-10-27", "2026-10-28"})

PY = sys.executable
CLI = r"C:\Users\yanzh\AppData\Local\Temp\pt-worker-t46"


def run(extra):
    p = subprocess.run([PY, "-m", "paper_trading.cli",
                        "--data-db", str(data), "--account-db", str(acct),
                        "--lock-file", str(WORK / "smoke.lock"),
                        "m3", "verify", "--today", TODAY, "--symbols", "000651",
                        "--days", "10"] + extra, cwd=CLI, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


print("=== 人读输出（钱类全绿；心跳/影子留痕应显式说不）===")
rc, out, err = run([])
print(out[-1600:] or err[-800:])
print("退出码 =", rc)

print("\n=== --json 结构 ===")
rc2, out2, err2 = run(["--json"])
payload = json.loads(out2)
d = payload.get("data") or {}
print("today=%s green=%s warn=%s red=%s skipped=%s rollback=%s money_reds=%s" % (
    d.get("today"), d.get("green"), d.get("warn"), d.get("red"), d.get("skipped"),
    d.get("rollback_required"), d.get("money_reds")))
for i in d.get("items") or []:
    print("  %-4s %-22s %s | %s" % ("钱" if i["money"] else "  ", i["id"], i["status"],
                                    i["detail"][:90]))
print("退出码(json) =", rc2)

print("\n=== 把成交费用改坏 ⇒ 必须红且 exit 3 ===")
c2 = sqlite3.connect(str(acct))
c2.execute("UPDATE fills SET commission=99.0 WHERE symbol='000651'")
c2.commit()
c2.close()
rc3, out3, err3 = run([])
print(out3[-900:] or err3[-500:])
print("退出码 =", rc3, "（期望 3 = 钱类红 ⇒ 立即回退）")

print("\n=== --money-only 只看钱类 ===")
rc4, out4, _ = run(["--money-only", "--json"])
d4 = (json.loads(out4).get("data") or {})
print("items:", [i["id"] for i in d4.get("items") or []], "rollback=", d4.get("rollback_required"))
print("退出码 =", rc4)
