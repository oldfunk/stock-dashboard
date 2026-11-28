"""参数研究流程（D3）：样本内 → 样本外 → 前向观察，**不自动上生产**。

复用 `engine.optimize.walk_forward`（与 engine walkforward 同口径），额外给出：
- `decision`：`candidate` / `overfit_risk` / `reject`；
- `auto_apply=false`：本模块不写 `config.yaml` / `engine.local.yaml`；
- `forward` 占位：前向观察需要真实后续交易日，禁止用样本外冒充前向；
- `guardrails`：数据指纹、样本切分、成交数、过拟合差距。

研究结论只供人/AI 决策，落生产必须人工改配置并重新走发布/观察。
"""
from __future__ import annotations

from typing import Optional

from paper_trading.engine.backtest import FeeModel
from paper_trading.engine.config import load_engine_config
from paper_trading.engine.optimize import GRIDS, INTRADAY_GRIDS, walk_forward
from paper_trading.utils.config import load_config


def classify(out: dict, *, min_oos_trades: int = 5) -> dict:
    """把 walk-forward 结果翻译成可执行/拒绝的建议（不自动写配置）。"""
    if not out or out.get("error"):
        return {"decision": "reject", "reason": out.get("error") if out else "无结果",
                "auto_apply": False}
    oos_ex = out.get("in_best_oos_excess_pct")
    trades = None
    rows = out.get("out_of_sample") or []
    if rows:
        trades = rows[0].get("oos_trades")
    gap = out.get("overfit_gap_pct")
    n_pos = int(out.get("n_oos_positive") or 0)
    n_tested = int(out.get("n_tested") or 0)
    reasons = []
    decision = "candidate"
    if oos_ex is None:
        decision = "reject"
        reasons.append("样本外无有效结果")
    elif float(oos_ex) <= 0:
        decision = "reject"
        reasons.append(f"样本内第 1 名在样本外超额 {oos_ex}% ≤ 0")
    elif trades is not None and int(trades) < int(min_oos_trades):
        decision = "reject"
        reasons.append(f"样本外成交 {trades} 笔 < {min_oos_trades}，样本不足")
    if gap is not None and abs(float(gap)) >= 5.0:
        if decision == "candidate":
            decision = "overfit_risk"
        reasons.append(f"过拟合差距 {gap}pp ≥ 5pp（事后最优与样本内第1名差距大）")
    if n_tested and n_pos < max(2, n_tested // 2) and decision == "candidate":
        decision = "overfit_risk"
        reasons.append(f"样本外仅 {n_pos}/{n_tested} 个变体为正超额")
    return {
        "decision": decision,
        "reasons": reasons,
        "auto_apply": False,
        "next_step": ("人工复核参数/回测，改配置后重新走发布观察；"
                      "禁止把本结果直接写入生产"),
        "thresholds": {"min_oos_trades": int(min_oos_trades),
                       "overfit_gap_warn_pp": 5.0},
    }


def run_research(bridge, *, grid: str = "ma_cross",
                 symbols: Optional[list[str]] = None,
                 days: int = 250, cash: float = 100000.0,
                 top: int = 5, split: float = 0.6,
                 value: float = 10000.0,
                 min_oos_trades: int = 5) -> dict:
    if grid not in GRIDS:
        return {"ok": False, "error": f"未知网格 {grid}（可选：{','.join(GRIDS)}）"}
    syms = sorted({str(s).strip() for s in (symbols or []) if s})
    if not syms:
        syms = sorted({str(s) for s in getattr(bridge, "stock_pool", [])}
                      | {p.symbol for p in bridge.broker.get_all_positions()})
    if not syms:
        return {"ok": False, "error": "没有可研究标的（config/持仓均为空）"}
    days = max(40, min(int(days), 1000))
    bars_by_sym: dict = {}
    all_days: set[str] = set()
    for s in syms:
        bars = bridge.data_db.get_bars(s, limit=days + 80)
        if bars:
            bars_by_sym[s] = bars
            all_days.update(b.timestamp.date().isoformat() for b in bars)
    if not bars_by_sym:
        return {"ok": False, "error": "本地无日线数据，先跑 sync"}
    day_list = sorted(all_days)[-days:]
    cfg_path = getattr(bridge, "config_path", "config.yaml") or "config.yaml"
    cfg = load_engine_config(cfg_path)
    full = load_config(cfg_path)
    tcfg = full["trading_config"]
    fee = FeeModel.from_trading_config(tcfg)
    risk = full.get("risk") or {}
    minutes = {}
    if grid in INTRADAY_GRIDS:
        from paper_trading.engine.backtest import load_minutes

        minutes = load_minutes(bridge.data_db, syms, day_list)
    out = walk_forward(cfg, bars_by_sym, day_list, grid, syms,
                       value=float(value), fee=fee, initial_cash=float(cash),
                       top=int(top), split=float(split),
                       slippage_fixed=float(tcfg.slippage_fixed),
                       risk=risk, minutes_by_day=minutes,
                       min_trades=1)
    verdict = classify(out, min_oos_trades=min_oos_trades)
    from paper_trading.engine.provenance import bars_fingerprint

    out["data_fingerprint"] = bars_fingerprint(
        bars_by_sym, day_list, minutes or None)
    out["research"] = {
        "decision": verdict["decision"],
        "reasons": verdict["reasons"],
        "auto_apply": False,
        "next_step": verdict["next_step"],
        "forward": {"status": "pending",
                    "note": "前向观察需真实后续交易日；样本外不等于前向"},
        "symbols": sorted(bars_by_sym),
        "days": len(day_list),
        "grid": grid,
        "cash": float(cash),
        "note": "研究结果不自动写配置/规则；生产变更须人工复核并重新观察",
    }
    out["ok"] = not out.get("error")
    out.setdefault("error", None)
    return out
