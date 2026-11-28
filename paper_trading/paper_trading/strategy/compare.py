"""默认策略对照（A6）：MA20/60 新默认 vs MA5/20 旧默认。

边界：
- 纯本地日线回测，无 LLM、无网络、不碰账本；
- 两个变体用**同一窗口、同一费用、同一风控、同一初始资金**，只改均线参数；
- 同时给出全窗口、样本内（前 70%）、样本外（后 30%）三段；
  “样本外”只是该历史窗口的后段，**不是真实前向验证**，更不是收益承诺；
- 数据不足/某标的没有 K 线时如实列出，不补零。
"""
from __future__ import annotations

from typing import Optional

from paper_trading.engine.backtest import Backtester, FeeModel, buy_and_hold, metrics


def _rule(symbols: list[str], short: int, long: int) -> dict:
    return {"id": f"ma{short}_{long}", "type": "ma_cross",
            "symbols": list(symbols),
            "params": {"short": int(short), "long": int(long)},
            "action": {"side": "buy", "value": 10000.0},
            "enabled": True}


def _run(bars_by_sym: dict, days: list[str], rule: dict, fee, risk: dict,
         cash: float) -> dict:
    cfg = {"rules": [rule], "min_ticket_cny": 0.0,
           "max_orders_per_day": 0, "max_turnover_per_day": 0.0}
    res = Backtester(cfg, fee=fee, slippage_pct=0.0,
                     slippage_fixed=0.01, risk=risk).run(
        bars_by_sym, days, initial_cash=cash)
    m = metrics(res, len(days))
    bench = buy_and_hold(bars_by_sym, days, cash, fee)
    if bench:
        m["benchmark_buy_hold_pct"] = bench["total_return_pct"]
        m["excess_pct"] = round(m.get("total_return_pct", 0.0)
                                - bench["total_return_pct"], 2)
    m["skipped_sample"] = res.skipped[:10]
    return m


def compare_default(bridge, *, days: int = 120,
                    symbols: Optional[list[str]] = None,
                    cash: Optional[float] = None) -> dict:
    days = max(40, min(int(days), 500))
    syms = sorted({str(s) for s in (symbols or []) if s})
    if not syms:
        syms = sorted({*getattr(bridge, "stock_pool", []),
                       *(p.symbol for p in bridge.broker.get_all_positions())})
    if not syms:
        return {"ok": False, "error": "没有可回测的股票池（config/持仓均为空）"}
    lookback = days + 80
    bars_by_sym: dict[str, list] = {}
    missing: list[str] = []
    for s in syms:
        bars = bridge.data_db.get_bars(s, limit=lookback)
        if bars:
            bars_by_sym[s] = bars
        else:
            missing.append(s)
    if not bars_by_sym:
        return {"ok": False, "error": "本地无日线数据，先跑 sync",
                "missing": missing}
    all_days = sorted({b.timestamp.date().isoformat()
                       for bars in bars_by_sym.values() for b in bars})
    eval_days = all_days[-days:]
    if len(eval_days) < 40:
        return {"ok": False,
                "error": f"可用交易日不足（{len(eval_days)} < 40），不编造结论"}

    from paper_trading.utils.config import load_config

    cfg = load_config(bridge.config_path)
    fee = FeeModel.from_trading_config(cfg["trading_config"])
    risk = dict(cfg.get("risk") or {})
    cash0 = float(cash or 100000.0)
    cut = max(1, int(len(eval_days) * 0.7))
    windows = {
        "full": eval_days,
        "in_sample": eval_days[:cut],
        "out_of_sample": eval_days[cut:],
    }
    variants = [{"name": "ma20_60", "label": "新默认 MA20/60",
                 "short": 20, "long": 60},
                {"name": "ma5_20_legacy", "label": "旧默认 MA5/20",
                 "short": 5, "long": 20}]
    out_variants = []
    for v in variants:
        rule = _rule(list(bars_by_sym), v["short"], v["long"])
        runs = {}
        for wname, wdays in windows.items():
            if len(wdays) < 20:
                runs[wname] = {"error": f"交易日不足（{len(wdays)} < 20）"}
                continue
            runs[wname] = _run(bars_by_sym, wdays, rule, fee, risk, cash0)
        out_variants.append({**v, "runs": runs})

    # 同窗口对照：全窗口总收益/回撤/夏普/费用
    table = []
    for v in out_variants:
        full = v["runs"].get("full") or {}
        table.append({
            "variant": v["name"], "label": v["label"],
            "total_return_pct": full.get("total_return_pct"),
            "excess_pct": full.get("excess_pct"),
            "max_drawdown_pct": full.get("max_drawdown_pct"),
            "sharpe_annual": full.get("sharpe_annual"),
            "trades": full.get("trades"),
            "fee_drag_pct": full.get("fee_drag_pct"),
        })
    return {
        "ok": True,
        "window": {"start": eval_days[0], "end": eval_days[-1],
                   "trading_days": len(eval_days),
                   "in_sample": [eval_days[0], eval_days[cut - 1]],
                   "out_of_sample": ([eval_days[cut], eval_days[-1]]
                                     if cut < len(eval_days) else None)},
        "symbols": sorted(bars_by_sym), "missing": missing,
        "initial_cash": cash0,
        "fee_model": "config.yaml trading 段（佣金/印花税/过户费）",
        "risk": risk or "未配置（回测器默认）",
        "variants": out_variants,
        "comparison": table,
        "disclaimer": ("历史回测，不是收益承诺；两个变体只改均线参数，"
                       "样本外仅是该历史窗口后段，不等于真实前向验证。"
                       "参数研究仍须走样本内→样本外→前向，不得直接上生产。"),
    }
