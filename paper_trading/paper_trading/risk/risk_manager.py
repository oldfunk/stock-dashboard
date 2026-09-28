"""风控模块。"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from paper_trading.models import Order, Position, Signal, TradingConfig
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class RiskManager:
    """
    风控管理器。

    职责：
    - 单笔订单金额上限
    - 单只股票持仓上限
    - 总仓位上限
    - 最大回撤止损
    """

    def __init__(
        self,
        max_single_order_value: float = 200_000.0,
        max_position_pct: float = 0.3,       # 单只股票最多30%仓位
        max_total_position_pct: float = 0.95,  # 总仓位不超过95%
        max_drawdown_pct: float = 0.20,      # 最大回撤20%止损
    ) -> None:
        self.max_single_order_value = max_single_order_value
        self.max_position_pct = max_position_pct
        self.max_total_position_pct = max_total_position_pct
        self.max_drawdown_pct = max_drawdown_pct
        self._peak_value: float = 0.0

    def check_signal(
        self,
        signal: Signal,
        current_price: float,
        cash: float,
        positions: Dict[str, Position],
        total_value: float,
        prices: Optional[Dict[str, float]] = None,
    ) -> tuple[bool, str]:
        """
        检查信号是否通过风控。

        Args:
            prices: 可选的全市场价格表 {symbol: price}，用于多标的总仓位计算；
                缺省时回退到 current_price（兼容单标的老调用）。
        """
        order_value = signal.volume * current_price

        # 1. 单笔金额上限
        if order_value > self.max_single_order_value:
            return False, f"Order value {order_value:.0f} exceeds max {self.max_single_order_value:.0f}"

        # 2. 买入: 检查资金
        if signal.direction.value == 1 and order_value > cash:
            return False, f"Insufficient cash: need {order_value:.0f}, have {cash:.0f}"

        # 3. 卖出: 检查可用持仓
        if signal.direction.value == -1:
            pos = positions.get(signal.symbol)
            if not pos or pos.available_volume < signal.volume:
                return False, f"Insufficient available volume for {signal.symbol}"

        # 4. 单只股票仓位上限
        if signal.direction.value == 1 and total_value > 0:
            pos = positions.get(signal.symbol)
            current_pos_value = (pos.total_volume if pos else 0) * current_price
            new_pos_value = current_pos_value + order_value
            if new_pos_value / total_value > self.max_position_pct:
                return False, f"Position limit exceeded for {signal.symbol}"

        # 5. 总仓位上限（多标的按各自价格计算，参考 rqalpha 持仓市值口径）
        if signal.direction.value == 1 and total_value > 0:
            def _px(sym: str) -> float:
                if prices and sym in prices:
                    return prices[sym]
                if sym == signal.symbol:
                    return current_price
                return 0.0

            total_pos_value = sum(p.total_volume * _px(sym) for sym, p in positions.items())
            new_total = total_pos_value + order_value
            if new_total / total_value > self.max_total_position_pct:
                return False, "Total position limit exceeded"

        return True, ""

    def update_peak(self, total_value: float) -> None:
        """更新峰值净值。"""
        if total_value > self._peak_value:
            self._peak_value = total_value

    def check_drawdown(self, total_value: float) -> tuple[bool, float]:
        """
        检查是否触发最大回撤止损。

        Returns:
            (是否触发, 当前回撤比例)
        """
        if self._peak_value <= 0:
            return False, 0.0
        drawdown = (self._peak_value - total_value) / self._peak_value
        return drawdown >= self.max_drawdown_pct, drawdown
