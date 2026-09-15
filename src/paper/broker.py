"""M4a BrokerAdapter 接口 + PaperBroker 桩实现（paper-trading.md §4.5）

设计说明：
- BrokerAdapter 是纸盘与未来实盘（M4c QmtBroker / PTradeBroker）共用的
  最小接口：place_order / cancel_order / get_positions / get_account。
  纸盘 UI 与未来实盘 UI 共用同一套持仓/委托/净值展示，切 broker 不换界面。
- PaperBroker 是库内撮合的桩：委托只落 paper_orders（submitted），
  撮合/费用/风控在后续迭代接入，接口签名保持不变。
- QmtBroker 归 M4c（需 Windows + xtquant + 券商账户），本模块绝不引入
  Windows 依赖；xtquant 即使将来要用也必须在 M4c 模块内懒加载。
- 纯 stdlib + 项目内 DAO；import 本模块无任何落盘副作用
  （建表/建账只发生在方法调用时，经由 DAO）。
"""
from abc import ABC, abstractmethod

from src.models.database import (
    PaperAccountDAO,
    PaperOrderDAO,
    PaperPositionDAO,
)


class BrokerAdapter(ABC):
    """交易通道抽象基类（M4c 预埋接口，现在只定签名）"""

    @abstractmethod
    def place_order(self, code, direction, price, volume) -> str:
        """下单，返回 broker 侧 order_id"""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单，成功返回 True"""
        ...

    @abstractmethod
    def get_positions(self) -> list:
        """当前持仓列表"""
        ...

    @abstractmethod
    def get_account(self) -> dict:
        """账户资金快照"""
        ...


class PaperBroker(BrokerAdapter):
    """纸盘 broker 桩：委托落库（submitted），撮合后续迭代。

    撤单规则：仅 submitted 状态可撤，成功置 cancelled；
    已成交/已撤/风控拦截/不存在的委托返回 False。
    """

    CANCELABLE = ("submitted",)
    DIRECTIONS = ("BUY", "SELL")

    def __init__(self, orders=None, positions=None, account=None):
        # DAO 无状态、构造器不碰库；允许注入以便单测替换
        self._orders = orders or PaperOrderDAO()
        self._positions = positions or PaperPositionDAO()
        self._account = account or PaperAccountDAO()

    def place_order(self, code, direction, price, volume,
                    signal_source=None) -> str:
        """下委托并落 paper_orders，返回 str(order_id)"""
        if direction not in self.DIRECTIONS:
            raise ValueError(f"direction 须为 {self.DIRECTIONS}，实得 {direction!r}")
        if price is None or price <= 0:
            raise ValueError(f"price 须 > 0，实得 {price!r}")
        if not isinstance(volume, int) or isinstance(volume, bool) or volume <= 0:
            raise ValueError(f"volume 须为正整数，实得 {volume!r}")
        kwargs = {}
        if signal_source is not None:
            kwargs["signal_source"] = signal_source
        oid = self._orders.place(code, direction, float(price), volume,
                                 **kwargs)
        return str(oid)

    def cancel_order(self, order_id: str) -> bool:
        """仅 submitted 可撤；非法 id / 非可撤状态返回 False"""
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return False
        order = self._orders.get(oid)
        if order is None or order["status"] not in self.CANCELABLE:
            return False
        return self._orders.update_status(oid, "cancelled")

    def get_positions(self) -> list:
        """当前持仓（paper_positions 全量）"""
        return self._positions.list_all()

    def get_account(self) -> dict:
        """账户快照（不存在则按默认 100 万建账）"""
        return self._account.get_or_create()
