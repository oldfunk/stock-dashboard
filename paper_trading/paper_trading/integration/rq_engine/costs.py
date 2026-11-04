"""费用口径对齐（T38·裁决 5）：把过户费补进 rqalpha，并把佣金/印花税钉到现网费率。

**为什么必须动这里**：影子对账的验收信号是 `fee_delta` 归零（§5.5 裁决 5），
而两侧费用口径原本差两处：

1. **佣金**：rqalpha 的 `StockTransactionCostDecider` 默认万八
   （`deciders.py:73 self.commission_rate = 0.0008`，倍率默认 1，
   `mod/rqalpha_mod_sys_transaction_cost/__init__.py:__config__`），
   现网账本是万 2.5 + 单笔最低 5 元（`paper_broker.py:255-266` +
   `config.yaml trading.commission_rate`）。之前 T29/T32 实测"费用差恰好等于
   过户费"是因为金额小到**两侧都撞上了 5 元最低佣金**，把佣金差盖住了——
   金额一大就会露出来（20 万元的单子：万八 160 元 vs 万 2.5 五十元）。
2. **过户费**：框架**根本没有这一项**（`calc` 只返回 commission/tax，
   `deciders.py:112-113`），现网按 0.001% **双向**收。

**做法（不改框架、不重写撮合）**：
- 佣金/印花税走框架自己的参数：`sys_transaction_cost` 的倍率换算
  （`stock_commission_multiplier = 现网率 / 0.0008`，`tax_multiplier = 印花 / 0.0005`），
  最低佣金交给框架的 per-order `commission_map`（`deciders.py:42-68`），
  与旧引擎"单笔最低"同形状；
- 过户费用 `TransferFeeOverlay` **包住**框架已注册的 decider，把
  `price × quantity × rate` 加进 `TransactionCost.other_fees`
  （`interface.py:745-752` 的 `.total` 会带上 → `position_model.py:293/426`
  扣现金、`matcher/base.py:108-116` 资金充足性校验也算进去）。

**为什么是包住而不是重造**：重造要把佣金倍率、最低佣金、印花税率各抄一遍，
`config.yaml` 一改就静默漂移；包住之后佣金/印花税的唯一真相仍在框架配置里，
本模块只负责它缺的那一项。

**只给 CS 股票加过户费**：框架把股票 decider 注册给了 CS/LOF/INDX/REITs
（`mod.py:134-140`，ETF 与 PUBLIC_FUND 另有类），而真实的过户费规则
（中国结算 0.001%，双向）**只对股票成立**，基金/REITs 我们没找到可信口径 ⇒ 不加。
现网池 10 只全是沪深主板股票，所以今天不受影响；哪天池子进了 LOF/REITs，
影子对账会用 `fee_delta` 把缺口喊出来，而不是被我们猜的费率糊过去。
"""
from __future__ import annotations

from typing import Any, Optional

# rqalpha 框架的基准费率（用来把现网率换算成倍率）——**这不是现网费率**，
# 是框架源码里的默认值，唯一作用是当除数（`deciders.py:73/78`）。
RQ_STOCK_COMMISSION_RATE = 0.0008    # deciders.py:73
RQ_STOCK_TAX_RATE = 0.0005           # deciders.py:78

# T51（纪律 7 口径唯一）：这里原本抄了四个现网费率常量
# （COMMISSION_RATE/COMMISSION_MIN/STAMP_DUTY_RATE/TRANSFER_FEE_RATE），
# 于是"不传 fee 就用现网默认"变成第二条真相源——config.yaml 改了引擎照旧按旧数跑。
# 现在费率**必须由调用方显式传入**（`runner.build_config` 从 `TradingConfig` 取），
# 拿不到就报错，不许在这里留第二份数字。


def stock_cost_mod_config(*, commission_rate: float,
                          commission_min: float,
                          stamp_duty_rate: float) -> dict:
    """`config.mod.sys_transaction_cost` 参数（纯函数，进会话指纹）。

    倍率是**除法换算**，所以传 0 会得 0 倍率（等于免佣）而不是报错——
    费率本来就该能配成 0，这里不做多余校验。
    """
    return {
        "enabled": True,
        "stock_commission_multiplier": float(commission_rate) / RQ_STOCK_COMMISSION_RATE,
        "stock_min_commission": float(commission_min),
        "tax_multiplier": float(stamp_duty_rate) / RQ_STOCK_TAX_RATE,
        # 现网印花税一直按 0.05% 固定收（不看历史时点），所以关掉框架的 PIT 税率：
        # 开了会让回测里 2023-08-28 之前变成千 1，影子对账凭空多一块差异。
        "pit_tax": False,
    }


_OVERLAY_CLS: Optional[type] = None


def make_overlay_class() -> type:
    """构造过户费 overlay decider 类（延迟 import rqalpha，NEC 未装引擎也能导本模块）。

    **必须缓存**：`install_transfer_fee` 的防重入靠 `isinstance(decider, Overlay)`，
    每次新建一个同名类会让判据永远为假 ⇒ 重复安装就是收两遍过户费。
    """
    global _OVERLAY_CLS
    if _OVERLAY_CLS is not None:
        return _OVERLAY_CLS

    from rqalpha.interface import TransactionCost
    from rqalpha.mod.rqalpha_mod_sys_transaction_cost.deciders import (
        AbstractStockTransactionCostDecider)

    class TransferFeeOverlay(AbstractStockTransactionCostDecider):
        """包住框架 decider，只补 `other_fees`；佣金/印花税原样透传。

        必须继承 `AbstractStockTransactionCostDecider`：`order_target_portfolio`
        会用 isinstance 卡这个类型（`api/order_target_portfolio.py:270-280`），
        纯 `AbstractTransactionCostDecider` 的子类会让 order_target_* API 直接 RuntimeError。
        """

        def __init__(self, inner: Any, rate: float) -> None:
            self.inner = inner
            self.rate = float(rate)
            self.applied = 0          # 证据：本会话补收了几笔过户费
            self.added_total = 0.0    # 证据：累计补收金额（元）

        def calc(self, args: Any) -> Any:
            cost = self.inner.calc(args)
            fee = abs(float(args.price) * int(args.quantity)) * self.rate
            self.applied += 1
            self.added_total += fee
            return TransactionCost(commission=cost.commission, tax=cost.tax,
                                   other_fees=(cost.other_fees or 0.0) + fee)

        def batch_estimate(self, delta_quantities: Any, prices: Any) -> Any:
            inner_est = self.inner.batch_estimate(delta_quantities, prices)
            return inner_est + delta_quantities.abs() * prices * self.rate

    _OVERLAY_CLS = TransferFeeOverlay
    return _OVERLAY_CLS


def install_transfer_fee(env: Any, rate: float,
                         *, types: Optional[tuple] = None,
                         market: Any = None) -> list[str]:
    """把已注册的股票 decider 包一层过户费，返回被包裹的 instrument_type 名。

    幂等：已经是 overlay 的不再套第二层（重复套会重复收费）。
    """
    from rqalpha.const import INSTRUMENT_TYPE

    if types is None:
        types = (INSTRUMENT_TYPE.CS,)
    Overlay = make_overlay_class()
    store = getattr(env, "_transaction_cost_deciders", {})
    wrapped: list[str] = []
    for (itype, imarket), decider in list(store.items()):
        if itype not in tuple(types) or isinstance(decider, Overlay):
            continue
        if market is not None and imarket != market:
            continue
        env.set_transaction_cost_decider(itype, Overlay(decider, rate), market=imarket)
        wrapped.append(str(getattr(itype, "value", itype)))
    return wrapped


def overlay_stats(env: Any, *, types: Optional[tuple] = None) -> list[dict]:
    """读回 overlay 的证据（进 evidence json，让"补了没补"可核）。"""
    from rqalpha.const import INSTRUMENT_TYPE

    if types is None:
        types = (INSTRUMENT_TYPE.CS,)
    out: list[dict] = []
    for (itype, _m), decider in list(getattr(env, "_transaction_cost_deciders", {}).items()):
        if itype in tuple(types) and getattr(decider, "applied", None) is not None:
            out.append({"instrument_type": str(getattr(itype, "value", itype)),
                        "rate": decider.rate, "applied": decider.applied,
                        "added_total": round(decider.added_total, 6)})
    return out


def transfer_fee_amount(amount: float, rate: float) -> float:
    """现网口径的过户费（旧引擎侧同一公式，测试用来交叉核对）。

    `rate` 必须显式给（T51：这里不留默认值，否则又是一条第二真相源）。
    """
    return abs(float(amount)) * float(rate)
