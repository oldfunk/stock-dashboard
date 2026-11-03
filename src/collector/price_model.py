"""三列价格模型：不复权(raw) + 后复权(hfq) → close_raw / close_adj / close。

2026-10-26 首版在 ``scripts/backfill_price_history.py``；2026-10-29 上移 src 层
（U9 方案 A，Issue #9）——每日增量写路径（``fetch_kline_data``）也要产出同一
三列模型，src 不允许反向 import scripts，故合流于此。脚本侧保留同名 re-export，
旧 import 路径不受影响。

口径（§9.1 契约）：
- ``close_raw``：不复权（估值 PE/PB 用）
- ``close_adj``：后复权（回测总收益用，恒正）
- ``close``：后复权按最新价缩放（= 正数前复权，展示用；不再出现负价）。
  锚点=两侧数据的最新交集日，故最新一行恒有 ``close == close_raw``。

**零价不进模型（批13-e，2026-11-03）**：上游把无成交/停牌日写成 ``0.00``
（产线实证：北交所 184 行 close=0，全部夹在正常价之间，920000 有一段
2019-07~2020-03 的长停牌）。0 进契约列会破坏比值与收益——收益序列里
``10 → 0 → 9`` 是 -100%/+900%，而真值是"那几天没有成交"。所以这里按
**fail-closed：停牌=无数据**，``close<=0`` 的输入行整行丢弃（母方对存量的
处置是同一口径：价格列置 NULL，volume/amount 保留 0）。
"""


def _price(value) -> bool:
    """这一侧的收盘价是不是一个"真价格"（有值且为正）。"""
    return value is not None and value > 0


def merge_price_model(raw_rows: list[dict], adj_rows: list[dict]) -> list[dict]:
    """合并不复权与后复权，生成三列价格。

    - ``close_raw``：不复权（估值 PE/PB 用）
    - ``close_adj``：后复权（回测总收益用，恒正）
    - ``close``：后复权按最新价缩放（= 正数前复权，展示用；不再出现负价）

    任一侧收盘价缺失或 <=0 的日子**不产出行**（停牌=无数据），锚点因此落在
    最后一个有真成交的交集日上——``scale`` 用的是"最新真价格"，停牌日不会把
    整窗口的缩放拉成 0/NaN。
    """
    raw = {r["trade_date"]: r for r in raw_rows if _price(r.get("close"))}
    adj = {r["trade_date"]: r for r in adj_rows if _price(r.get("close"))}
    dates = sorted(set(raw) & set(adj))
    if not dates:
        return []
    last = dates[-1]
    scale = raw[last]["close"] / adj[last]["close"]
    out = []
    for d in dates:
        a, r = adj[d], raw[d]
        out.append({
            "trade_date": d,
            "open": a["open"] * scale if a.get("open") is not None else None,
            "close": a["close"] * scale,
            "high": a["high"] * scale if a.get("high") is not None else None,
            "low": a["low"] * scale if a.get("low") is not None else None,
            "volume": r.get("volume"),
            "amount": r.get("amount"),
            "turnover": r.get("turnover"),
            "close_raw": r["close"],
            "close_adj": a["close"],
            "shares": r.get("shares"),
        })
    return out
