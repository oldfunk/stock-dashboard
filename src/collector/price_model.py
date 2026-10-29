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
"""


def merge_price_model(raw_rows: list[dict], adj_rows: list[dict]) -> list[dict]:
    """合并不复权与后复权，生成三列价格。

    - ``close_raw``：不复权（估值 PE/PB 用）
    - ``close_adj``：后复权（回测总收益用，恒正）
    - ``close``：后复权按最新价缩放（= 正数前复权，展示用；不再出现负价）
    """
    raw = {r["trade_date"]: r for r in raw_rows if r.get("close") is not None}
    adj = {r["trade_date"]: r for r in adj_rows if r.get("close") is not None}
    dates = sorted(set(raw) & set(adj))
    if not dates:
        return []
    last = dates[-1]
    if not adj[last]["close"]:
        return []
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
