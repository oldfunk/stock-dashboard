"""纸盘执行质量（E2，2026-11-28）：成交价 vs 当日 VWAP/区间。

只读纸盘 fills + 母库日线；不写账本、不下单。
- VWAP = amount / volume（kline_daily 单位：元、股）；
- 缺 kline 时退 daily_close（amount 万元、volume 手），换算后同口径；
- 输出逐笔偏离（bps）与汇总，供执行质量抽样复算。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.database import db_conn  # noqa: E402
from src.paper_bridge import load_recent_fills  # noqa: E402


def direction_label(d) -> str:
    return "买入" if int(d or 0) > 0 else "卖出"


def execution_row(fill: dict, bar: dict | None) -> dict:
    """单笔成交 vs 当日 bar；bar 缺失时指标为 None，不猜。"""
    price = float(fill.get("price") or 0)
    fee = sum(float(fill.get(k) or 0)
              for k in ("commission", "stamp_duty", "transfer_fee"))
    row = {
        "date": str(fill.get("timestamp") or "")[:10],
        "symbol": str(fill.get("symbol") or "").zfill(6),
        "direction": direction_label(fill.get("direction")),
        "volume": int(fill.get("volume") or 0),
        "price": price,
        "fee": round(fee, 4),
        "vwap": None, "dev_bps": None, "range_pos": None,
        "close": None, "dev_close_bps": None,
    }
    if not bar:
        return row
    amount, volume = bar.get("amount"), bar.get("volume")
    vwap = (float(amount) / float(volume)
            if amount and volume else None)
    row["vwap"] = round(vwap, 4) if vwap else None
    if vwap:
        row["dev_bps"] = round((price / vwap - 1.0) * 10000, 1)
    high, low = bar.get("high"), bar.get("low")
    if high is not None and low is not None and high > low:
        row["range_pos"] = round((price - float(low)) / (float(high) - float(low)), 3)
    close = bar.get("close")
    if close:
        row["close"] = round(float(close), 4)
        row["dev_close_bps"] = round((price / float(close) - 1.0) * 10000, 1)
    return row


def summarize(rows: list[dict]) -> dict:
    def avg(key, subset):
        vals = [r[key] for r in subset if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None
    by_dir = {}
    for d in ("买入", "卖出"):
        sub = [r for r in rows if r["direction"] == d]
        by_dir[d] = {"n": len(sub), "avg_dev_bps": avg("dev_bps", sub)}
    return {
        "n": len(rows),
        "fees": round(sum(r["fee"] for r in rows), 2),
        "avg_dev_bps": avg("dev_bps", rows),
        "avg_dev_close_bps": avg("dev_close_bps", rows),
        "by_direction": by_dir,
    }


def _bar(conn, code: str, day: str) -> dict | None:
    row = conn.execute(
        "SELECT high, low, close, amount, volume FROM kline_daily "
        "WHERE code = ? AND trade_date = ?", (code, day)).fetchone()
    if row and row["close"] is not None:
        return dict(row)
    row = conn.execute(
        "SELECT NULL AS high, NULL AS low, close, amount, volume FROM daily_close "
        "WHERE code = ? AND trade_date = ?", (code, day)).fetchone()
    if row:
        d = dict(row)
        # daily_close: amount 万元、volume 手 → 元、股
        if d.get("amount") is not None:
            d["amount"] = float(d["amount"]) * 1e4
        if d.get("volume") is not None:
            d["volume"] = float(d["volume"]) * 100
        return d
    return None


def collect(limit: int = 200) -> list[dict]:
    fills = load_recent_fills(limit=limit)
    if fills.get("source") == "unavailable":
        raise SystemExit(f"纸盘成交不可用：{fills.get('reason')}")
    rows = []
    with db_conn() as conn:
        for f in fills.get("data") or []:
            day = str(f.get("timestamp") or "")[:10]
            code = str(f.get("symbol") or "").zfill(6)
            rows.append(execution_row(f, _bar(conn, code, day) if day else None))
    return rows


def render(rows: list[dict], summary: dict) -> str:
    if not rows:
        return "无成交记录"
    lines = ["# 纸盘执行质量（成交价 vs 当日 VWAP）", "",
             "| 日期 | 代码 | 方向 | 数量 | 成交价 | VWAP | 偏离(bps) | "
             "区间位置 | 收盘 | 对收盘(bps) | 费用 |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(
            f"| {r['date']} | {r['symbol']} | {r['direction']} | {r['volume']} | "
            f"{r['price']:.3f} | {r['vwap'] if r['vwap'] is not None else '--'} | "
            f"{r['dev_bps'] if r['dev_bps'] is not None else '--'} | "
            f"{r['range_pos'] if r['range_pos'] is not None else '--'} | "
            f"{r['close'] if r['close'] is not None else '--'} | "
            f"{r['dev_close_bps'] if r['dev_close_bps'] is not None else '--'} | "
            f"{r['fee']:.4f} |")
    lines += ["", f"共 {summary['n']} 笔；费用合计 {summary['fees']:.2f} 元；"
                  f"平均 VWAP 偏离 {summary['avg_dev_bps']} bps；"
                  f"平均对收盘偏离 {summary['avg_dev_close_bps']} bps。"]
    for d, s in summary["by_direction"].items():
        lines.append(f"- {d} {s['n']} 笔，平均 VWAP 偏离 {s['avg_dev_bps']} bps")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="纸盘执行质量（只读）")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = collect(limit=args.limit)
    summary = summarize(rows)
    if args.json:
        print(json.dumps({"rows": rows, "summary": summary},
                         ensure_ascii=False, indent=2))
    else:
        print(render(rows, summary))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
