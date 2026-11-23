"""20 日高突破策略（T90）：只用本地日线，不依赖母库 / LLM。"""
from __future__ import annotations

from typing import Dict

from paper_trading.models import Bar, Signal, SignalType
from paper_trading.strategy.base_strategy import BaseStrategy
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class BreakoutStrategy(BaseStrategy):
    """收盘突破前 N 日高点买入；跌破前 M 日低点卖出（趋势跟随）。"""

    params_schema = {
        "high_window": {"type": int, "default": 20, "min": 2, "max": 250},
        "exit_window": {"type": int, "default": 10, "min": 2, "max": 120},
        "buy_volume": {"type": int, "default": 100, "min": 100, "max": 100000},
        "sell_volume": {"type": int, "default": 100, "min": 100, "max": 100000},
    }

    def __init__(self, high_window: int = 20, exit_window: int = 10,
                 buy_volume: int = 100, sell_volume: int = 100) -> None:
        super().__init__(name=f"BO_{high_window}_{exit_window}")
        self.high_window = int(high_window)
        self.exit_window = int(exit_window)
        self.buy_volume = int(buy_volume)
        self.sell_volume = int(sell_volume)

    def generate_signals(self, bars: Dict[str, list[Bar]]) -> Dict[str, Signal]:
        out: Dict[str, Signal] = {}
        need = max(self.high_window, self.exit_window) + 1
        for symbol, rows in (bars or {}).items():
            if len(rows) < need:
                continue
            closes = [float(b.close) for b in rows]
            last = closes[-1]
            prior_high = max(closes[-self.high_window - 1:-1])
            prior_low = min(closes[-self.exit_window - 1:-1])
            if last > prior_high:
                out[symbol] = Signal(
                    symbol=symbol, direction=SignalType.BUY,
                    volume=self.buy_volume, price=last,
                    reason=f"突破 {self.high_window} 日收盘高点 {prior_high:.2f}")
                logger.info(f"Signal: BUY {symbol} @ {last:.2f} (breakout)")
            elif last < prior_low:
                out[symbol] = Signal(
                    symbol=symbol, direction=SignalType.SELL,
                    volume=self.sell_volume, price=last,
                    reason=f"跌破 {self.exit_window} 日收盘低点 {prior_low:.2f}")
                logger.info(f"Signal: SELL {symbol} @ {last:.2f} (breakdown)")
        return out
