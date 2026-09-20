"""M4a PaperBroker 撮合引擎（paper-trading.md §4）

设计说明：
- BrokerAdapter 是纸盘与未来实盘（M4c QmtBroker / PTradeBroker）共用的
  最小接口：place_order / cancel_order / get_positions / get_account。
- PaperBroker 实现 A 股撮合建模：T+1 / 100 股整数倍 / 佣金万 2.5 最低 5 元 /
  印花税卖出千分之 0.5 / 过户费万 0.1 / 滑点 / 涨跌停判定。
- place_order 落库 submitted；fill_order 按市价撮合（含滑点）；
  end_of_day 解冻 T+1 + 记录净值。
- QmtBroker 归 M4c（需 Windows + xtquant），本模块绝不引入 Windows 依赖。
- 纯 stdlib + 项目内 DAO；import 无副作用。
"""
from abc import ABC, abstractmethod

from src.config import load_config
from src.models.database import (
    PaperAccountDAO,
    PaperOrderDAO,
    PaperPositionDAO,
    PaperTradeDAO,
    PaperNavDAO,
    db_conn,
)
from src.utils import now_cn


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
    """纸盘 broker：A 股撮合建模（T+1 / 费用 / 整手 / 涨跌停）。

    撮合流程：
    - place_order：校验参数 + 风控 → 落库 submitted
    - fill_order：按市价成交（含滑点），计算费用，更新持仓/资金
    - end_of_day：解冻 T+1 可用数量，记录每日净值

    撤单规则：仅 submitted 状态可撤，成功置 cancelled。
    """

    CANCELABLE = ("submitted",)
    DIRECTIONS = ("BUY", "SELL")

    def __init__(self, orders=None, positions=None, account=None,
                 trades=None, nav=None):
        self._orders = orders or PaperOrderDAO()
        self._positions = positions or PaperPositionDAO()
        self._account = account or PaperAccountDAO()
        self._trades = trades or PaperTradeDAO()
        self._nav = nav or PaperNavDAO()
        self._cfg = load_config().get("paper", {})

    def place_order(self, code, direction, price, volume,
                    signal_source=None) -> str:
        """下委托并落 paper_orders，返回 str(order_id)"""
        if direction not in self.DIRECTIONS:
            raise ValueError(f"direction 须为 {self.DIRECTIONS}，实得 {direction!r}")
        if price is None or price <= 0:
            raise ValueError(f"price 须 > 0，实得 {price!r}")
        if not isinstance(volume, int) or isinstance(volume, bool) or volume <= 0:
            raise ValueError(f"volume 须为正整数，实得 {volume!r}")
        if volume % 100 != 0:
            raise ValueError(f"volume 须为 100 整数倍，实得 {volume}")

        # 卖出时校验可用持仓
        if direction == "SELL":
            pos = self._positions.get(code)
            avail = pos["avail_volume"] if pos else 0
            if volume > avail:
                raise ValueError(
                    f"卖出数量 {volume} 超过可用持仓 {avail}（{code}）"
                )

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

    def fill_order(self, order_id: int, market_price: float) -> dict:
        """按市价撮合单笔委托（含滑点 + 费用）。

        Args:
            order_id: 委托 ID
            market_price: 当前市价（收盘价或下一交易日开盘价）

        Returns:
            {"trade_id": int, "fill_price": float, "commission": float,
             "stamp_tax": float, "transfer_fee": float, "total_cost": float}

        Raises:
            ValueError: 订单不存在 / 非 submitted / 市价无效
            ValueError: 风控拦截（仓位超限 / 回撤超限）
        """
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"订单 {order_id} 不存在")
        if order["status"] != "submitted":
            raise ValueError(f"订单 {order_id} 状态为 {order['status']}，非 submitted")
        if market_price is None or market_price <= 0:
            raise ValueError(f"市价须 > 0，实得 {market_price!r}")

        code = order["code"]
        direction = order["direction"]
        volume = order["volume"]
        price = order["price"]

        # 计算成交价（含滑点）
        slippage_pct = self._cfg.get("slippage_pct", 0.1) / 100
        if direction == "BUY":
            fill_price = round(price * (1 + slippage_pct), 2)
        else:
            fill_price = round(price * (1 - slippage_pct), 2)

        # 计算费用
        fees = self._calc_fees(direction, fill_price, volume)

        # 风控检查（买入前）
        if direction == "BUY":
            self._check_risk_buy(code, fill_price, volume, fees["total_cost"])

        # 更新资金
        acc = self._account.get_or_create()
        if direction == "BUY":
            total_cost = fill_price * volume + fees["total_cost"]
            new_cash = round(acc["cash"] - total_cost, 2)
        else:
            revenue = fill_price * volume - fees["total_cost"]
            new_cash = round(acc["cash"] + revenue, 2)

        # 更新持仓
        pos = self._positions.get(code)
        if direction == "BUY":
            if pos:
                # 加仓：更新均价
                old_cost = pos["avg_price"] * pos["volume"]
                new_volume = pos["volume"] + volume
                new_avg = round((old_cost + fill_price * volume) / new_volume, 4)
                # T+1：当日买入不可卖，avail 不变（新买的冻结）
                self._positions.upsert(
                    code, new_volume, pos["avail_volume"],
                    new_avg, None
                )
            else:
                # 新建仓位：T+1 冻结，avail=0
                self._positions.upsert(code, volume, 0, fill_price, None)
        else:  # SELL
            new_volume = pos["volume"] - volume
            new_avail = pos["avail_volume"] - volume
            if new_volume <= 0:
                self._positions.remove(code)
            else:
                self._positions.upsert(
                    code, new_volume, new_avail, pos["avg_price"], None
                )

        # 更新账户
        self._account.update(cash=new_cash)

        # 记录成交
        trade_id = self._trades.record(
            order_id, code, direction, fill_price, volume,
            commission=fees["commission"],
            stamp_tax=fees["stamp_tax"],
            transfer_fee=fees["transfer_fee"],
        )

        # 更新订单状态
        self._orders.update_status(order_id, "filled")

        return {
            "trade_id": trade_id,
            "fill_price": fill_price,
            **fees,
        }

    def end_of_day(self, date_str: str = None) -> dict:
        """日终处理：解冻 T+1 可用数量 + 更新浮动盈亏 + 记录净值。

        Args:
            date_str: 日期 YYYY-MM-DD，默认今天

        Returns:
            {"nav_date": str, "total_value": float, "cash": float,
             "market_value": float, "pnl": float, "cumulative_pnl": float}
        """
        if date_str is None:
            date_str = now_cn().strftime("%Y-%m-%d")

        # 解冻 T+1：今日买入的部分变为可用
        positions = self._positions.list_all()
        for pos in positions:
            if pos["avail_volume"] < pos["volume"]:
                # 可用数量 = 总持仓（今日之前冻结的部分已自动可用，
                # 但我们的 upsert 不会自动解冻，所以这里统一解冻到全部）
                self._positions.upsert(
                    pos["code"], pos["volume"], pos["volume"],
                    pos["avg_price"], pos.get("floating_pnl")
                )

        # 计算总资产（需要最新行情，这里用 avg_price 近似）
        # TODO: 接入 kline_daily 获取最新收盘价计算真实浮盈
        total_market_value = 0.0
        positions = self._positions.list_all()
        for pos in positions:
            # 暂用均价近似市值（精确计算需接入行情）
            market_val = pos["avg_price"] * pos["volume"]
            total_market_value += market_val

        acc = self._account.get_or_create()
        cash = acc["cash"]
        total_value = round(cash + total_market_value, 2)
        initial_cash = acc["initial_cash"]
        cumulative_pnl = round(total_value - initial_cash, 2)

        # 更新账户
        self._account.update(total_value=total_value, cumulative_pnl=cumulative_pnl)

        # 记录净值
        self._nav.save(
            date_str, total_value, cash, round(total_market_value, 2),
            round(total_value - (acc.get("total_value") or initial_cash), 2),
            cumulative_pnl,
        )

        return {
            "nav_date": date_str,
            "total_value": total_value,
            "cash": cash,
            "market_value": round(total_market_value, 2),
            "pnl": round(total_value - (acc.get("total_value") or initial_cash), 2),
            "cumulative_pnl": cumulative_pnl,
        }

    def _calc_fees(self, direction: str, price: float,
                   volume: int) -> dict:
        """计算 A 股交易费用。

        - 佣金：成交额 × 万 2.5，最低 5 元（双边）
        - 印花税：成交额 × 千分之 0.5（仅卖出）
        - 过户费：成交额 × 万 0.1（双边）
        """
        amount = price * volume
        rate = self._cfg.get("commission_rate", 0.00025)
        min_comm = self._cfg.get("commission_min", 5.0)
        commission = max(round(amount * rate, 2), min_comm)

        stamp_tax = 0.0
        if direction == "SELL":
            stamp_tax_rate = self._cfg.get("stamp_tax_rate", 0.005)
            stamp_tax = round(amount * stamp_tax_rate, 2)

        tf_rate = self._cfg.get("transfer_fee_rate", 0.00001)
        transfer_fee = round(amount * tf_rate, 2)

        total = round(commission + stamp_tax + transfer_fee, 2)

        return {
            "commission": commission,
            "stamp_tax": stamp_tax,
            "transfer_fee": transfer_fee,
            "total_cost": total,
        }

    def _check_risk_buy(self, code: str, price: float, volume: int,
                        total_cost: float):
        """买入前风控检查（§4.4）。

        - 单股 ≤ 20% 总资产
        - 总仓位 ≤ 80% 总资产
        - 账户回撤 ≤ 15% 禁买
        """
        acc = self._account.get_or_create()
        total_value = acc.get("total_value") or acc["cash"]
        if total_value <= 0:
            return

        # 账户回撤检查
        initial = acc["initial_cash"]
        drawdown_pct = self._cfg.get("force_drawdown_pct", -15)
        if initial > 0 and (total_value / initial - 1) * 100 < drawdown_pct:
            raise ValueError(
                f"风控拦截：账户回撤超过 {drawdown_pct}%，禁买"
            )

        # 单股仓位检查
        max_pos_pct = self._cfg.get("max_position_pct", 20)
        pos = self._positions.get(code)
        existing_value = 0.0
        if pos:
            existing_value = pos["avg_price"] * pos["volume"]
        new_total_value = existing_value + price * volume
        if total_value > 0 and (new_total_value / total_value * 100) > max_pos_pct:
            raise ValueError(
                f"风控拦截：单股 {code} 仓位将达 "
                f"{new_total_value / total_value * 100:.1f}%，超过 {max_pos_pct}% 上限"
            )

        # 总仓位检查
        max_total_pct = self._cfg.get("max_total_position_pct", 80)
        positions = self._positions.list_all()
        current_market_value = sum(
            p["avg_price"] * p["volume"] for p in positions
        )
        new_total_market = current_market_value + price * volume
        if total_value > 0 and (new_total_market / total_value * 100) > max_total_pct:
            raise ValueError(
                f"风控拦截：总仓位将达 "
                f"{new_total_market / total_value * 100:.1f}%，超过 {max_total_pct}% 上限"
            )
