"""参数网格搜索：在历史数据上批量回测规则变体，用**超额收益**排序。

目的不是"找出历史上最好看的参数"，而是提供一个可复现的筛选器：
同一段数据、同一套费率、同一基准，参数变体之间可比。
样本外验证（walk-forward）与过拟合防护留给上层（见 docs 建议）。

诚实边界：分钟线只覆盖最近几个交易日，所以日内网格（vwap_dev / minute_ma）
的样本天然很小，排序结果只能当"待验证线索"，不能当结论。
"""
from __future__ import annotations

import itertools
from typing import Iterable, Optional

from paper_trading.engine.backtest import (Backtester, FeeModel,
                                            buy_and_hold, metrics)
from paper_trading.engine.rules import INTRADAY_TYPES, RULE_TYPES

#: 依赖分钟线的网格（需要 minutes_by_day 才有意义）
INTRADAY_GRIDS = ("vwap_dev", "minute_ma")

# 预置网格：参数组合是"有先验"的常规区间，不是无脑笛卡尔积
GRIDS: dict[str, dict] = {
    "ma_cross": {
        "base_type": "ma_cross",
        "grid": {"short": [5, 10, 20], "long": [20, 30, 60]},
        "filter": lambda p: int(p["short"]) < int(p["long"]),
    },
    "rsi_zone": {
        "base_type": "rsi_zone",
        "grid": {"n": [6, 14], "lower": [25, 30], "upper": [70, 75]},
        "filter": lambda p: float(p["lower"]) < float(p["upper"]),
    },
    "trailing_stop": {
        "base_type": "trailing_stop",
        "grid": {"pct": [3, 5, 8, 12]},
        "filter": lambda p: True,
    },
    # 日内网格（需分钟线）
    "vwap_dev": {
        "base_type": "vwap_dev",
        "grid": {"pct": [0.5, 1, 2, 3], "min_bars": [5, 15]},
        "filter": lambda p: True,
    },
    "minute_ma": {
        "base_type": "minute_ma_cross",
        "grid": {"short": [3, 5, 8], "long": [15, 30, 60]},
        "filter": lambda p: int(p["short"]) < int(p["long"]),
    },
}

#: 网格名 → 规则类型（minute_ma 是别名，落到 minute_ma_cross）
_GRID_TYPE = {name: spec["base_type"] for name, spec in GRIDS.items()}


def _variants(grid_name: str, symbols: list[str], value: float,
              sell_side: bool = True) -> list[dict]:
    """按网格生成规则变体（交叉类金叉买 + 死叉卖 成对，提高可用性）。"""
    spec = GRIDS.get(grid_name)
    if not spec or not symbols:
        return []
    rtype = spec["base_type"]
    paired = grid_name in ("ma_cross", "rsi_zone")
    keys = list(spec["grid"].keys())
    out: list[dict] = []
    for combo in itertools.product(*(spec["grid"][k] for k in keys)):
        params = dict(zip(keys, combo))
        if not spec["filter"](params):
            continue
        tag = "-".join(f"{params[k]}" for k in keys)
        for sym in symbols:
            out.append({"id": f"g:{grid_name}:{tag}:buy:{sym}", "type": rtype,
                        "symbol": sym, "params": dict(params),
                        "action": {"side": "buy", "value": value}, "once": False})
            if sell_side and paired:
                out.append({"id": f"g:{grid_name}:{tag}:sell:{sym}", "type": rtype,
                            "symbol": sym, "params": dict(params),
                            "action": {"side": "sell", "all": True}, "once": False})
    return out


def _rules_for(grid_name: str, symbol: str, params: dict, value: float) -> list[dict]:
    """按（网格、标的、参数）重建该组规则（样本外复验要用同一套规则）。"""
    out = []
    for r in _variants(grid_name, [symbol], value):
        if r["symbol"] != symbol:
            continue
        if r["params"] == dict(params):
            out.append(r)
    return out


def _evaluate(cfg: dict, rules: list[dict], bars_by_sym: dict, days: list[str],
              fee: FeeModel, initial_cash: float, slippage_pct: float,
              minutes_by_day: Optional[dict], slippage_fixed: float = 0.01,
              risk: Optional[dict] = None) -> dict:
    """跑一组规则并返回可比较的指标（样本内/样本外同一口径）。"""
    trial = dict(cfg)
    trial["rules"] = rules
    trial.setdefault("min_ticket_cny", 1000)
    trial.setdefault("max_orders_per_day", 10 ** 6)
    trial["max_turnover_per_day"] = 0
    intraday = any(str(r.get("type") or "") in INTRADAY_TYPES for r in rules)
    res = Backtester(trial, fee=fee, slippage_pct=slippage_pct,
                     slippage_fixed=slippage_fixed, risk=risk).run(
        bars_by_sym, days, initial_cash=initial_cash,
        minutes_by_day=minutes_by_day if intraday else None)
    m = metrics(res, len(days))
    bench = buy_and_hold(bars_by_sym, days, initial_cash, fee)
    bench_ret = bench.get("total_return_pct", 0.0)
    pf = m.get("profit_factor")
    return {
        "ret_pct": m.get("total_return_pct", 0.0),
        "mdd_pct": m.get("max_drawdown_pct", 0.0),
        "sharpe": m.get("sharpe_annual", 0.0),
        "trades": m.get("trades", 0),
        "fees": m.get("total_fees", 0.0),
        "profit_factor": pf if isinstance(pf, (int, float)) else None,
        "benchmark_pct": bench_ret,
        "excess_pct": round(m.get("total_return_pct", 0.0) - bench_ret, 2),
        "intraday_days": res.intraday_days,
    }


def grid_search(cfg: dict, bars_by_sym: dict[str, list], days: list[str],
                grid_name: str, symbols: Optional[list[str]] = None,
                value: float = 10000.0, fee: Optional[FeeModel] = None,
                initial_cash: float = 100000.0,
                slippage_pct: float = 0.0,
                slippage_fixed: float = 0.01,
                risk: Optional[dict] = None,
                top: int = 10,
                minutes_by_day: Optional[dict] = None,
                min_trades: int = 1) -> dict:
    """对单个网格做参数搜索；返回按超额收益排序的变体结果。

    min_trades：至少成交多少笔才算"一条策略"。**默认剔除 0 笔的变体**——
    熊市里"什么都不做"会以 0% 收益跑赢任何真亏钱的策略，把这种空结果排第一
    等于给出"最佳参数是不交易"这种荒谬结论。它们单独统计在 dropped 里。
    """
    fee = fee or FeeModel()
    symbols = list(symbols or [])
    variants = _variants(grid_name, symbols, value)
    if not variants:
        return {"error": f"网格 {grid_name} 无有效变体或未指定标的",
                "grids": list(GRIDS)}
    minutes_by_day = minutes_by_day or {}
    # min_trades=0 显式表示"不过滤"；None/缺省为 1（剔除 0 笔的空结果）
    _min_trades = 1 if min_trades is None else max(0, int(min_trades))
    intraday = grid_name in INTRADAY_GRIDS
    if intraday and not minutes_by_day:
        return {"error": f"网格 {grid_name} 依赖分钟线，本地没有分钟数据"
                          f"（先 minute pull）", "grids": list(GRIDS)}
    bench = buy_and_hold(bars_by_sym, days, initial_cash, fee)
    bench_ret = bench.get("total_return_pct", 0.0)

    # 按参数分组评估（金叉+死叉是一组，不能拆开单算）
    groups: dict[str, list[dict]] = {}
    for r in variants:
        tag = r["id"].split(":")[2] + ":" + r["symbol"]
        groups.setdefault(tag, []).append(r)

    results = []
    dropped: list[dict] = []
    intraday_days = 0
    for tag, rules in groups.items():
        m = _evaluate(cfg, rules, bars_by_sym, days, fee, initial_cash,
                      slippage_pct, minutes_by_day if intraday else None,
                      slippage_fixed=slippage_fixed, risk=risk)
        intraday_days = max(intraday_days, int(m.get("intraday_days") or 0))
        rec = {
            "tag": tag, "symbol": rules[0]["symbol"], "params": rules[0]["params"],
            "ret_pct": m["ret_pct"], "mdd_pct": m["mdd_pct"],
            "sharpe": m["sharpe"], "trades": m["trades"],
            "win_pct": 0.0,
            "profit_factor": m["profit_factor"],
            "fees": m["fees"],
            "excess_pct": m["excess_pct"],
        }
        if int(m.get("trades") or 0) < _min_trades:
            dropped.append(rec)
            continue
        results.append(rec)
    results.sort(key=lambda x: (x["excess_pct"], -x["mdd_pct"]), reverse=True)
    dropped.sort(key=lambda x: -x["excess_pct"])
    best = results[0] if results else None
    note = "超额=变体收益−等权买入持有；样本内结果，需样本外复验"
    if intraday:
        note += "；分钟线只覆盖最近数日，样本极小，结论仅供参考"
    if dropped:
        note += (f"；另有 {len(dropped)} 组参数在该样本内几乎不触发（成交不足 "
                 f"{_min_trades} 笔），已剔除不参与排名——否则熊市里「什么都不做」"
                 f"会排第一")
    return {"grid": grid_name, "rule_type": _GRID_TYPE.get(grid_name),
            "symbols": symbols, "days": len(days),
            "intraday": intraday,
            "intraday_days": intraday_days if intraday else 0,
            "benchmark_pct": bench_ret, "variants": len(results),
            "dropped_no_trade": len(dropped),
            "dropped_top": dropped[:3],
            "best": best, "top": results[:top],
            "note": note}


def walk_forward(cfg: dict, bars_by_sym: dict[str, list], days: list[str],
                 grid_name: str, symbols: Optional[list[str]] = None,
                 value: float = 10000.0, fee: Optional[FeeModel] = None,
                 initial_cash: float = 100000.0, slippage_pct: float = 0.0,
                 slippage_fixed: float = 0.01, risk: Optional[dict] = None,
                 top: int = 5, split: float = 0.6,
                 minutes_by_day: Optional[dict] = None,
                 min_trades: int = 1) -> dict:
    """样本内选参数 → 样本外检验（walk-forward）。

    这是"参数网格"能不能用的关键：样本内排第一的参数，换到没见过的数据上
    往往就不行了（过拟合）。这里把数据按时间切成两段：
    前 split 比例用来排名，后段用来检验，并给出"事后最优"作对照——
    **样本内第一名与样本外第一名的差距，就是过拟合的量度**。
    """
    fee = fee or FeeModel()
    days = list(days or [])
    if len(days) < 20:
        return {"error": f"样本太短（{len(days)} 天），无法做样本外检验"}
    cut = max(10, min(len(days) - 5, int(len(days) * float(split))))
    is_days, oos_days = days[:cut], days[cut:]
    in_sample = grid_search(cfg, bars_by_sym, is_days, grid_name, symbols,
                            value=value, fee=fee, initial_cash=initial_cash,
                            slippage_pct=slippage_pct,
                            slippage_fixed=slippage_fixed, risk=risk, top=top,
                            minutes_by_day=minutes_by_day, min_trades=min_trades)
    if in_sample.get("error"):
        return in_sample
    intraday = grid_name in INTRADAY_GRIDS
    oos_rows = []
    for rec in in_sample.get("top") or []:
        rules = _rules_for(grid_name, rec["symbol"], rec["params"], value)
        if not rules:
            continue
        m = _evaluate(cfg, rules, bars_by_sym, oos_days, fee, initial_cash,
                      slippage_pct, minutes_by_day if intraday else None,
                      slippage_fixed=slippage_fixed, risk=risk)
        oos_rows.append({
            "symbol": rec["symbol"], "params": rec["params"],
            "in_excess_pct": rec["excess_pct"], "in_trades": rec["trades"],
            "oos_excess_pct": m["excess_pct"], "oos_ret_pct": m["ret_pct"],
            "oos_trades": m["trades"], "oos_sharpe": m["sharpe"],
            "oos_mdd_pct": m["mdd_pct"],
        })
    oos_rows.sort(key=lambda x: -x["oos_excess_pct"])
    # 事后最优（用样本外挑出来的第一名）——用来量过拟合差距
    oracle = oos_rows[0] if oos_rows else None
    picked = oos_rows[0] if oos_rows else None
    n_pos = len([r for r in oos_rows if r["oos_excess_pct"] > 0])
    return {
        "grid": grid_name,
        "split": {"in_sample": [is_days[0], is_days[-1]],
                  "out_of_sample": [oos_days[0], oos_days[-1]],
                  "in_days": len(is_days), "oos_days": len(oos_days)},
        "in_sample_top": in_sample.get("top") or [],
        "out_of_sample": oos_rows,
        "n_oos_positive": n_pos,
        "n_tested": len(oos_rows),
        "sample_in_best_excess": (in_sample.get("top") or [{}])[0].get("excess_pct"),
        "oracle_oos_excess": oracle.get("oos_excess_pct") if oracle else None,
        "note": ("样本内排名 → 样本外检验。若样本外明显掉下来（甚至转负），"
                 "说明参数是过拟合出来的，不能写进生产规则集。"),
    }


def available_grids() -> Iterable[str]:
    return tuple(GRIDS)