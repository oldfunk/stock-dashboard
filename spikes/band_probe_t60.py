"""T60 现网对照：raw 缺 T-1 那一天，当日涨跌停带会算成什么（在库副本上跑，只读生产）。

A) 现在（raw 已补到 11-02）：正常供数，带 = 11-02 收盘 × 档位；
B) 把 11-02 那根删掉 = 今早 09:30 的真实状态：新代码拒供并给出原因，
   同时把"旧写法会拿哪天凑"算出来对照（10-30 的收盘）。
"""
import datetime
import pathlib
import shutil
import sqlite3

from paper_trading.data.db_manager import DataDBManager
from paper_trading.integration.rq_engine.bands import band_for
from paper_trading.integration.rq_engine.store import RawMinuteBarStore

TODAY = datetime.date.today().isoformat()
SRC = "/home/nec/paper-trading/data.db"
DB = "band_probe.db"
SYM = "002192"
OBID = "002192.XSHE"

pathlib.Path(DB).unlink(missing_ok=True)
shutil.copy(SRC, DB)


def raw_days(db):
    ro = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = ro.execute("SELECT substr(timestamp,1,10), close FROM daily_bars_raw"
                      " WHERE symbol=? ORDER BY timestamp", (SYM,)).fetchall()
    ro.close()
    return {d: float(c) for d, c in rows}


def probe(label, expect_prev):
    db = DataDBManager(DB)
    st = RawMinuteBarStore.from_data_db(db, [SYM], today=TODAY)
    days = raw_days(db)
    latest = max(days) if days else "无"
    print(f"\n{label}")
    print(f"  raw 最新日={latest}  当日={TODAY}  需要的前一交易日={expect_prev}")
    if st.gate_rejected:
        print(f"  拒供：{st.gate_rejected[SYM] if SYM in st.gate_rejected else st.gate_rejected}")
    bars = st.get_bars(OBID)
    if len(bars):
        pc, up = float(bars["prev_close"][-1]), float(bars["limit_up"][-1])
        print(f"  供出的当日带：prev_close={pc:.2f} limit_up={up:.2f} "
              f"limit_down={float(bars['limit_down'][-1]):.2f}")
    # 对照：如果按旧写法拿"最近可得的收盘"凑，会用哪一天的多少块当 prev_close
    stale = max((d for d in days if d < expect_prev), default=None)
    if stale:
        wrong_pc = days[stale]
        print(f"  旧写法会拿 {stale} 的收盘 {wrong_pc:.2f} 凑 ⇒ 今日涨停 "
              f"{wrong_pc * (1 + band_for(SYM)):.2f}（错 {(wrong_pc - days.get(expect_prev, wrong_pc)):+.2f} 元/股）")
    return st


probe("A) 现状（raw 已补到 11-02）", "2026-11-02")

c = sqlite3.connect(DB)
c.execute("DELETE FROM daily_bars_raw WHERE symbol=? AND substr(timestamp,1,10)>='2026-11-02'",
          (SYM,))
c.commit()
c.close()
probe("B) 删掉 11-02 那根 = 今早 09:30 的真实状态", "2026-11-02")
