"""双轨影子对照（M2 换引擎期间的"同日两本账"）。

同一交易日、同一份原始价输入，分别交给两个引擎：

- **rqalpha 侧**（`runner.run_session`）：撮合/T+1/涨跌停判定都在框架内；
- **旧自研侧**（`PaperBroker.submit_order`）：现网账本逻辑，作为对照基线。

两侧的产出（现金/持仓/费用）逐字段相减，差值就是"引擎语义差"。
差值超出容差 → 不合并、不停线，但**必须留痕**：`op_log[parity:diff]`
（§5.5 裁决 4 + 主 AI 批复 ①：默认写生产账本，`--no-op-log` 才关；
`record_to` 有 action 白名单断言，只许 `parity:diff` 这一个 action 从这里进 op_log）。

安全边界（红线 1）：本模块**只写自己影子目录里的库**，
`shadow_dir` 必填且必须与生产 `paper_account.db` 不同路径——
构造时会显式比对并拒绝相同路径，绝不误写现网账本。
唯一会碰生产库的动作是 `record_to` 往 `op_log` 追加一行审计记录，
它不改余额/持仓/NAV（`log_operation` 只 INSERT op_log）。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from paper_trading.utils.fees import as_fee_dict

from .runner import run_session

#: 现金容差（元）。
CASH_TOLERANCE = 1.0
#: 费用容差（元）。T38 裁决 5 落地后两侧费率口径完全一致（佣金/印花税走
#: `sys_transaction_cost` 倍率，过户费由 `costs.py` 的 overlay 补），
#: 验收信号就是 **fee_delta 归零**，所以这里从"相对 1%"收紧成绝对值。
#: 留 1 分是 float 乘加尾差与四舍五入的余量，不是"已知差异"的容身之处。
FEE_ABS_TOLERANCE = 0.01
#: op_log 白名单（裁决 4 的防护）：影子路径只允许这一个 action 落生产 op_log。
OP_LOG_ACTIONS = frozenset({"parity:diff"})


def _legacy_side(*, today: str, bars_by_symbol: dict, plan: list, shadow_db: Path,
                 init_cash: float, fee: Optional[dict] = None) -> dict:
    """旧引擎影子账：同一天按收盘价限价撮合，产出现金/持仓/费用。

    两侧必须**同一口径起点**，否则对账比的是参数差异而不是引擎语义差异：
    初始资金/滑点在这里显式对齐（rqalpha 侧滑点为 0 ⇒ 旧线侧也置 0），
    费率（`fee`）由 `shadow_day` 用 `as_fee_dict` 归一后**同一份**传给两侧：
    rqalpha 侧进 `sys_transaction_cost` 倍率 + 过户费 overlay（`costs.py`），
    旧线侧进 `TradingConfig` ⇒ 不存在"各拿各的默认值"。
    """
    from paper_trading.broker.paper_broker import PaperBroker
    from paper_trading.models import Order, TradingConfig
    from paper_trading.models.types import OrderType

    cfg = TradingConfig(**dict(fee or {}), initial_cash=float(init_cash),
                        slippage_fixed=0.0, slippage_pct=0.0, use_slippage_pct=False)
    broker = PaperBroker(db_path=shadow_db, config=cfg)
    fees = 0.0
    fills: list = []
    rejected: list = []
    for act in plan:
        if act.get("action") not in ("buy", "sell") or not act.get("volume"):
            continue
        sym = str(act["symbol"]).zfill(6)
        bars = bars_by_symbol.get(sym) or []
        if not bars:
            rejected.append({"symbol": sym, "reason": "no_bars"})
            continue
        last = bars[-1]
        prev_close = bars[-2].close if len(bars) >= 2 else None
        order = Order(symbol=sym, direction=1 if act["action"] == "buy" else -1,
                      volume=int(act["volume"]), order_type=OrderType.LIMIT,
                      limit_price=float(last.close), prev_close=prev_close,
                      ref_high=float(last.high), ref_low=float(last.low))
        res = broker.submit_order(order)
        status = str(getattr(res.status, "value", res.status)).upper()
        if res.filled_volume:
            cost = float((res.commission or 0) + (res.stamp_duty or 0) + (res.transfer_fee or 0))
            fees += cost
            fills.append({"symbol": sym, "side": "BUY" if res.direction == 1 else "SELL",
                          "volume": int(res.filled_volume),
                          "price": round(float(res.filled_price), 4),
                          "cost": round(cost, 4), "status": status})
        else:
            # 旧引擎的拒单原因只进 logger（paper_broker.py:291-295），
            # 对象与库里都没有——与 rqalpha 前端风控同病（G-T13-8 要覆盖两侧）
            rejected.append({"symbol": sym, "status": status, "reason": "see_logger"})
    cash = float(broker.get_cash())
    # 我们自己的 Position 用 total_volume/available_volume/avg_cost（models/types.py:83-91），
    # rqalpha 侧叫 quantity/closable/avg_price —— 对账前必须归一到同一形状
    positions = {p.symbol: {"quantity": int(p.total_volume),
                            "closable": int(p.available_volume),
                            "avg_price": round(float(p.avg_cost), 4)}
                 for p in broker.get_all_positions() if p.total_volume}
    return {"cash": round(cash, 4), "positions": positions,
            "fees": round(fees, 4), "fills": fills, "rejected": rejected}


def _rq_side(*, today: str, data_db: str, bundle: str, symbols: list, plan: list,
             shadow_dir: Path, init_cash: float, force: bool,
             fee: Optional[dict] = None) -> dict:
    """rqalpha 影子账：自己的状态目录，绝不与生产共用。"""
    res = run_session(today=today, data_db=data_db, bundle=bundle, symbols=symbols,
                      plan=plan, init_cash=init_cash,
                      state_dir=str(shadow_dir / "rq_state"),
                      workdir=str(shadow_dir / "rq"), force=force, fee=fee)
    post = (res.get("snapshots") or [{}])[-1] if res.get("snapshots") else {}
    ev = res.get("evidence") or {}
    fees = round(sum(t.get("cost") or 0 for t in ev.get("trades") or []), 4)
    from .store import to_symbol

    # 键统一到 6 位代码（rqalpha 用 order_book_id，旧引擎用 symbol）
    rq_pos = {to_symbol(k): {"quantity": v["quantity"], "avg_price": v["avg_price"]}
              for k, v in (post.get("positions") or {}).items() if v.get("quantity")}
    return {
        "cash": post.get("cash"), "total_value": post.get("total_value"),
        "positions": rq_pos,
        "fees": fees, "fills": ev.get("trades") or [],
        "rejects": ev.get("rejects") or [], "error": res.get("error"),
        "gates": res.get("gates"), "fingerprint": res.get("fingerprint"),
        "handler_dates": res.get("handler_dates"),
        # 过户费 overlay 到底装没装、收了几笔，跟着对账结果一起出（验收要能自查）
        "cost": ev.get("cost") or {},
    }


def parity_diff(rq: dict, legacy: dict, *, tolerance: float = CASH_TOLERANCE) -> dict:
    """逐字段对照（金额用容差，数量必须严格相等——A 股整手，不该有浮点差）。"""
    out = {"cash_delta": None, "fee_delta": None, "position_delta": {},
           "fill_delta": None, "violations": []}
    if rq.get("cash") is not None and legacy.get("cash") is not None:
        out["cash_delta"] = round(float(rq["cash"]) - float(legacy["cash"]), 4)
        if abs(out["cash_delta"]) > tolerance:
            out["violations"].append(f"cash_delta {out['cash_delta']} > {tolerance}")
    if rq.get("fees") is not None and legacy.get("fees") is not None:
        out["fee_delta"] = round(float(rq["fees"]) - float(legacy["fees"]), 4)
        if abs(out["fee_delta"]) > FEE_ABS_TOLERANCE:
            base = max(float(legacy["fees"]), 1e-9)
            out["violations"].append(
                f"fee_delta {out['fee_delta']} 超绝对容差 {FEE_ABS_TOLERANCE}"
                f"（相对 {out['fee_delta'] / base:.4%}）：先核对佣金/印花税倍率与"
                f"过户费 overlay 是否装上（evidence.cost.wrapped）")
    rq_pos, lg_pos = rq.get("positions") or {}, legacy.get("positions") or {}
    for sym in sorted(set(rq_pos) | set(lg_pos)):
        a = (rq_pos.get(sym) or {}).get("quantity", 0)
        b = (lg_pos.get(sym) or {}).get("quantity", 0)
        if a != b:
            out["position_delta"][sym] = {"rqalpha": a, "legacy": b, "delta": a - b}
            out["violations"].append(f"持仓不一致 {sym}: rq={a} legacy={b}")
    out["fill_delta"] = len(rq.get("fills") or []) - len(legacy.get("fills") or [])
    if out["fill_delta"]:
        out["violations"].append(f"成交笔数不一致 delta={out['fill_delta']}")
    return out


def shadow_day(*, today: str, data_db: str, bundle: str, symbols: list, plan: list,
               shadow_dir: str, production_account_db: Optional[str] = None,
               init_cash: float = 100_000.0, force: bool = False,
               fee: Optional[dict] = None,
               fresh: bool = True) -> dict:
    """同日双跑 + 对账。`shadow_dir` 必填；与生产账本同路径直接拒绝。

    `fee` 是**两侧共用**的费率口径来源（`TradingConfig` 或含四项费率的 dict）。
    T51 起必须显式给：`as_fee_dict` 缺项即报错——影子对账的意义就是"同一把尺子量两侧"，
    任何一侧偷偷用自己的默认值，`fee_delta` 就再也不可信。
    """
    fee_map = as_fee_dict(fee)
    sd = Path(shadow_dir).resolve()
    prod = Path(production_account_db).resolve() if production_account_db else None
    legacy_db = sd / "paper_account.db"
    if prod and legacy_db == prod:
        raise RuntimeError("影子对照禁止写生产账本：shadow 路径与 paper_account.db 相同")
    sd.mkdir(parents=True, exist_ok=True)
    if fresh and legacy_db.exists():
        legacy_db.unlink()

    from paper_trading.data.db_manager import DataDBManager

    db = DataDBManager(data_db)
    # 计划里的标的也要取 bar，否则影子侧会拿不到参考价
    want = {str(s).zfill(6) for s in symbols} | {str(a.get("symbol", "")).zfill(6)
                                                 for a in plan if a.get("symbol")}
    bars_by_symbol = {s: db.get_raw_bars(s, limit=30, end=f"{today}T23:59:59") for s in want}
    rq = _rq_side(today=today, data_db=str(data_db), bundle=bundle,
                  symbols=sorted(want), plan=plan, shadow_dir=sd, init_cash=init_cash,
                  force=force, fee=fee_map)
    legacy = _legacy_side(today=today, bars_by_symbol=bars_by_symbol, plan=plan,
                          shadow_db=legacy_db, init_cash=init_cash, fee=fee_map)
    diff = parity_diff(rq, legacy)
    return {"today": today, "plan": plan, "shadow_dir": str(sd),
            "fee": fee_map, "rqalpha": rq, "legacy": legacy, "diff": diff,
            "ok": not diff["violations"] and rq.get("error") is None}


def record_to(broker, res: dict, action: str = "parity:diff") -> None:
    """把对账结论写进 `op_log[parity:diff]`（默认路径，裁决 4）。

    白名单断言是拍板要求的防护：**只允许这一个 action 从这里进生产 op_log**，
    防止以后有人顺手拿同一个函数写别的动作，把"影子留痕"变成万能写入口。
    """
    if action not in OP_LOG_ACTIONS:
        raise ValueError(f"op_log 白名单只允许 {sorted(OP_LOG_ACTIONS)}，拒绝 {action!r}")
    broker.log_operation(action,
                         {"today": res["today"], "symbols": sorted(res["diff"]["position_delta"]),
                          "violations": res["diff"]["violations"]},
                         res["ok"],
                         {"cash_delta": res["diff"]["cash_delta"],
                          "fee_delta": res["diff"]["fee_delta"],
                          "fill_delta": res["diff"]["fill_delta"],
                          "rqalpha_cash": res["rqalpha"].get("cash"),
                          "legacy_cash": res["legacy"].get("cash")},
                         None, None)


def purge(shadow_dir: str) -> None:
    shutil.rmtree(Path(shadow_dir), ignore_errors=True)


__all__ = ["shadow_day", "parity_diff", "record_to", "purge", "OP_LOG_ACTIONS",
           "CASH_TOLERANCE", "FEE_ABS_TOLERANCE"]
