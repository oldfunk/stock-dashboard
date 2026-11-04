"""费用口径的唯一来源（T51·AGENTS 纪律 7：口径唯一）。

**为什么要有这个模块**：T43 架构地图点了"费率三个真相源"（`models/types.py`
TradingConfig、`engine/backtest.py` FeeModel、`rq_engine/costs.py` 模块常量），
T51 实测发现还要更糟——除了三份**数字**，还有三份**公式**
（broker 的 `_calc_buy_cost/_calc_sell_cost`、FeeModel 的 `buy_cost/sell_cost`、
`m3_verify.check_fees` 里重算的那一遍），以及三处"各自 new"
（`cli.py` 回测/网格两处 + `dashboard.py:_fee_of`）。

而 `report-t51.md §1` 记录的现网事实是：那三处 new 读的是
`load_config(cfg)["trading"]`——**`utils/config.py` 返回的键叫 `trading_config`，
没有 `trading`** ⇒ `.get("trading") or {}` 恒为 `{}` ⇒ 每一处都吃自己的硬编码兜底。
`config.yaml` 把佣金改成 0.001，回测与面板优化照旧按万 2.5 算，且**没有任何报错**。

本模块收口的两件事：
1. `fee_dict(cfg)`：把 `TradingConfig` 的四项费率打成引擎/影子要的形状（唯一构造点）；
2. `buy_cost/sell_cost`：**唯一的费用公式**——佣金 `max(额×率, 单笔地板)`、
   印花税只在卖出单边、过户费双向。旧线 broker、回测 FeeModel、`m3 verify`
   的重算全部改成调这里 ⇒ 三条路共用一把尺子。

费率**数值**的唯一来源仍是 `models/types.py:TradingConfig`（它的默认值就是现网口径），
`config.yaml` 的 `trading:` 段负责覆盖它——除此之外不许再出现第四个数字。
"""
from __future__ import annotations

#: 四项费率的键名（顺序即打出来的 dict 顺序；新增费率要改这一处）
FEE_KEYS = ("commission_rate", "commission_min", "stamp_duty_rate", "transfer_fee_rate")


def fee_dict(cfg) -> dict:
    """`TradingConfig` → 费率包（`rq_engine` 的 `fee` 参数、影子对账共用）。"""
    missing = [k for k in FEE_KEYS if not hasattr(cfg, k)]
    if missing:
        raise AttributeError(f"费率来源必须有 {FEE_KEYS}，缺 {missing}")
    return {k: float(getattr(cfg, k)) for k in FEE_KEYS}


def as_fee_dict(fee) -> dict:
    """把"费率来源"归一成四项 dict——`TradingConfig` 或 dict 都收，**缺项即报错**。

    存在的理由：调用方以前要么手搓 dict（三处各搓一遍，还搓错了键），要么干脆不传
    让下层用兜底数字。现在统一走这里：不传/缺项 ⇒ 大声失败，不兜底。
    """
    if fee is None:
        raise ValueError("必须显式给出费率来源（TradingConfig 或含四项费率的 dict）；"
                         "不给就报错——兜底数字是第二真相源（纪律 7）")
    if isinstance(fee, dict):
        out = {k: float(fee[k]) for k in FEE_KEYS if k in fee}
    else:
        out = fee_dict(fee)
    missing = [k for k in FEE_KEYS if k not in out]
    if missing:
        raise ValueError(f"费率来源缺 {missing}，四项缺一不可（现有 {sorted(out)}）")
    return out


def buy_cost(cfg, amount: float) -> tuple[float, float]:
    """买入费用：`(佣金, 过户费)`。佣金有单笔地板，过户费按成交额双向收。"""
    amount = float(amount)
    commission = max(amount * float(cfg.commission_rate), float(cfg.commission_min))
    return commission, amount * float(cfg.transfer_fee_rate)


def sell_cost(cfg, amount: float) -> tuple[float, float, float]:
    """卖出费用：`(佣金, 印花税, 过户费)`。印花税是卖出单边，买入侧恒 0。"""
    amount = float(amount)
    commission = max(amount * float(cfg.commission_rate), float(cfg.commission_min))
    return (commission, amount * float(cfg.stamp_duty_rate),
            amount * float(cfg.transfer_fee_rate))
