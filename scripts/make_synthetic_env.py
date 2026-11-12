"""合成数据环境生成器（2026-11-12）：给研究线一个"已知答案"的库。

为什么需要它
------------
研究线（H-001/H-002/H-003…）要验证的是**研究 OS 的端到端正确性**：
假设 -> 预注册 -> 快照 -> 实验 -> 结论。真实 outcome 一周才攒几十行、还受
外部 AI 分析写入方停滞影响（2026-10 整月 0 行），拿它验证研究 OS 等于
"等数据"而不是"验代码"。本脚本生成一个**内含已知 MOS->超额关系**的库，
研究 OS 跑在它上面应当**恢复出那个关系**——把"代码对不对"和"数据够不够"解耦。

生成内容
--------
- instrument / kline_daily / benchmark_daily / market_state_daily
- financial_history（含 interest_coverage / fcf，全字段）
- financial_summary / stock_snapshot
- **analysis_outcome（核心）**：bake 了
      excess_20  = 2.0 + 0.15*mos + N(0,4)
      excess_60  = 5.0 + 0.30*mos + N(0,6)
      excess_120 = 8.0 + 0.40*mos + N(0,8)
  即 MOS 每高 10pp，60 日超额高 3pp。研究 OS 应当给出"净增量为正"的结论。

用法
----
    python scripts/make_synthetic_env.py                      # 默认 100 只
    python scripts/make_synthetic_env.py --stocks 200 --seed 7

用合成库跑研究 OS（不动生产库）
    STOCK_DASHBOARD_DB=data/db/stock_dashboard_synthetic.db \\
        python -m src.research.hypothesis create ...
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

# 必须在 init_database() 之前把库路径指到合成库，否则建到生产库上
DEFAULT_OUT = PROJ / "data" / "db" / "stock_dashboard_synthetic.db"

# ── bake 进去的 ground truth（研究 OS 应当恢复出这些）────────────────────────
TRUE_EXCESS = {"20": (2.0, 0.15, 4.0), "60": (5.0, 0.30, 6.0), "120": (8.0, 0.40, 8.0)}


def _trading_days(start: date, end: date) -> list[str]:
    """工作日序列（合成环境不模拟节假日：形状对就行）。"""
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _codes(n: int) -> list[tuple[str, str, str]]:
    """(代码, 名称, 交易所)。沪市 60 开头、深市 00/30 开头。"""
    out = []
    for i in range(n):
        if i % 5 == 0:
            code, ex = f"601{i:03d}"[:6], "SH"
        elif i % 5 == 1:
            code, ex = f"600{i:03d}"[:6], "SH"
        elif i % 5 == 2:
            code, ex = f"000{i:03d}"[:6], "SZ"
        elif i % 5 == 3:
            code, ex = f"002{i:03d}"[:6], "SZ"
        else:
            code, ex = f"300{i:03d}"[:6], "SZ"
        out.append((code, f"合成{i:03d}", ex))
    return out


def build(out_path: Path, n_stocks: int = 100, seed: int = 42,
          slope_scale: float = 1.0) -> dict:
    import src.models.database as db

    db.get_db_path = lambda: str(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    db.init_database()

    # slope_scale=0 → 零关系对照库（研究 OS 应当判"不支持"，验假阳性）
    true_excess = {h: (b, s * slope_scale, sd)
                   for h, (b, s, sd) in TRUE_EXCESS.items()}

    rng = random.Random(seed)
    codes = _codes(n_stocks)
    days = _trading_days(date(2025, 1, 2), date(2026, 11, 12))
    if len(days) < 200:
        raise ValueError("交易日太少，调大区间")

    with db.db_conn() as conn:
        # ── instrument ──
        for code, name, ex in codes:
            conn.execute(
                "INSERT OR REPLACE INTO instrument (instrument_id, market_id, symbol,"
                " exchange, name, currency, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?, 'CNY','active','2025-01-01','2025-01-01')",
                (f"CN:{code}", "CN", code, ex, name))

        # ── kline_daily：随机游走 ──
        for code, name, ex in codes:
            price = rng.uniform(5, 60)
            for d in days:
                ret = rng.gauss(0.0004, 0.018)
                close = round(price * (1 + ret), 4)
                open_ = round(price, 4)
                high = round(max(open_, close) * (1 + abs(rng.gauss(0, 0.01))), 4)
                low = round(min(open_, close) * (1 - abs(rng.gauss(0, 0.01))), 4)
                vol = int(rng.uniform(1e6, 1e8))
                conn.execute(
                    "INSERT OR REPLACE INTO kline_daily (code, trade_date, open, close,"
                    " high, low, volume, amount, turnover, close_raw, close_adj, shares,"
                    " market, instrument_id, close_qfq)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (code, d, open_, close, high, low, vol, vol * close, 1.0,
                     close, close, 1e9, "A", f"CN:{code}", close))
                price = close

        # ── benchmark_daily ──
        b = 4000.0
        for d in days:
            b *= (1 + rng.gauss(0.0003, 0.010))
            conn.execute(
                "INSERT OR REPLACE INTO benchmark_daily (code, trade_date, close)"
                " VALUES (?,?,?)", ("sh000300", d, round(b, 2)))

        # ── market_state_daily ──
        for d in days:
            conn.execute(
                "INSERT OR REPLACE INTO market_state_daily (trade_date, metrics_json,"
                " missing_json, coverage, status, regime, regime_confidence,"
                " quality_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (d, '{"pe_median": 20.0}', "[]", 1.0, "pass", "risk_off", 0.9,
                 "{}", d + "T16:00:00"))

        # ── financial_history：8 个季度，ic/fcf 全字段 ──
        qends = ["2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30",
                 "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
        for code, name, ex in codes:
            roe = rng.uniform(3, 22)
            for q in qends:
                conn.execute(
                    "INSERT OR REPLACE INTO financial_history (stock_code, report_date,"
                    " report_type, roe, gross_margin, net_margin, ocf_per_share,"
                    " debt_ratio, revenue_growth, profit_growth, net_profit,"
                    " interest_coverage, fcf, total_shares, roic, eps, data_source,"
                    " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (code, q, "A" if q.endswith("12-31") else "Q",
                     round(roe, 2), round(rng.uniform(10, 60), 2),
                     round(rng.uniform(2, 25), 2), round(rng.uniform(-1, 5), 3),
                     round(rng.uniform(20, 70), 2), round(rng.uniform(-10, 30), 2),
                     round(rng.uniform(-10, 40), 2), rng.uniform(1e7, 1e10),
                     round(rng.uniform(2, 50), 2), rng.uniform(-1e8, 5e9),
                     1e9, round(rng.uniform(3, 25), 2), round(rng.uniform(0.1, 5), 3),
                     "synthetic", q + "T00:00:00"))

        # ── financial_summary / stock_snapshot ──
        for code, name, ex in codes:
            roe = rng.uniform(3, 22)
            conn.execute(
                "INSERT OR REPLACE INTO financial_summary (stock_code, roe_5y_avg,"
                " roe_5y_count, gross_margin_5y_avg, net_margin_5y_avg, ocf_5y_trend,"
                " ocf_latest, ocf_positive_years, debt_ratio_latest, net_profit_5y_sum,"
                " intcov_5y_avg, fcf_5y_sum, share_dilution_5y, roic_5y_avg,"
                " roe_10y_avg, net_margin_10y_avg, intcov_10y_avg, fcf_10y_sum,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, round(roe, 2), 5, round(rng.uniform(10, 60), 2),
                 round(rng.uniform(2, 25), 2), 1, round(rng.uniform(-1, 5), 3), 4,
                 round(rng.uniform(20, 70), 2), rng.uniform(1e8, 1e10),
                 round(rng.uniform(2, 50), 2), rng.uniform(-1e8, 5e9), 0.0,
                 round(rng.uniform(3, 25), 2), round(roe, 2),
                 round(rng.uniform(2, 25), 2), round(rng.uniform(2, 50), 2),
                 rng.uniform(-1e8, 5e9), "2026-11-12T16:00:00"))
            conn.execute(
                "INSERT OR REPLACE INTO stock_snapshot (code, name, market, sector, pe,"
                " pb, ps, market_cap, circulating_cap, roe, revenue, revenue_growth,"
                " profit, profit_growth, debt_ratio, dividend_yield, payout_ratio,"
                " dividend_year, dividend_years, snapshot_date)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, name, "A", rng.choice(["银行", "白酒", "医药", "电子", "机械"]),
                 round(rng.uniform(5, 60), 2), round(rng.uniform(0.5, 8), 2),
                 round(rng.uniform(0.5, 10), 2), round(rng.uniform(10, 2000), 2),
                 round(rng.uniform(10, 2000), 2), round(roe, 2),
                 round(rng.uniform(5, 500), 2), round(rng.uniform(-10, 30), 2),
                 round(rng.uniform(1, 200), 2), round(rng.uniform(-10, 40), 2),
                 round(rng.uniform(20, 70), 2), round(rng.uniform(0, 5), 2),
                 round(rng.uniform(0, 60), 2), "2025", rng.randint(0, 8),
                 "2026-11-12"))

        # ── analysis_outcome（核心）：bake 已知 MOS->超额关系 ──
        analysis_days = days[120::3]           # 2025 年中起，每隔 3 个交易日
        aid = 1
        for d in analysis_days:
            bench = {"20": rng.gauss(0, 4), "60": rng.gauss(0, 6), "120": rng.gauss(0, 8)}
            for k in range(20):
                code, name, ex = codes[(aid + k) % n_stocks]
                mos = rng.uniform(-20, 60)
                row = {"analysis_id": aid, "benchmark_code": "sh000300",
                       "stock_code": code, "stock_name": name, "analysis_date": d,
                       "signal": "BUY", "confidence": "中", "verdict": "通过",
                       "entry_date": d, "entry_price": 10.0, "mos_at_entry": round(mos, 2)}
                for h, (base, slope, sd) in true_excess.items():
                    ex_ret = base + slope * mos + rng.gauss(0, sd)
                    row[f"excess_{h}"] = round(ex_ret, 4)
                    row[f"ret_{h}"] = round(bench[h] + ex_ret, 4)
                    row[f"bench_{h}"] = round(bench[h], 4)
                row["updated_at"] = d + "T16:00:00"
                cols = ", ".join(row)
                q = ",".join("?" * len(row))
                conn.execute(
                    f"INSERT OR REPLACE INTO analysis_outcome ({cols}) VALUES ({q})",
                    list(row.values()))
                aid += 1

    with db.db_conn() as conn:
        n_rows = conn.execute("SELECT COUNT(*) FROM analysis_outcome").fetchone()[0]
    return {"out": str(out_path), "stocks": n_stocks, "days": len(days),
            "analysis_rows": n_rows, "true_excess": true_excess}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成合成数据环境（研究线快速验证）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出库路径")
    ap.add_argument("--stocks", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--slope-scale", type=float, default=1.0,
                    help="ground truth 斜率倍率；0 = 零关系对照库（验假阳性）")
    args = ap.parse_args(argv)
    stats = build(Path(args.out), args.stocks, args.seed, args.slope_scale)
    print(f"[合成库] {stats['out']}")
    print(f"  股票 {stats['stocks']} 只 / 交易日 {stats['days']} 天 / "
          f"analysis_outcome {stats['analysis_rows']} 行")
    print("  ground truth（研究 OS 应恢复出这些）:")
    for h, (base, slope, sd) in stats["true_excess"].items():
        print(f"    excess_{h} = {base} + {slope}*mos + N(0,{sd})")
    print("  用法: STOCK_DASHBOARD_DB=" + stats["out"] +
          " python -m src.research.experiment run <plan-id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
