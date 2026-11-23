"""RSI 区间策略（T90）：只用本地日线，不依赖母库 / LLM。"""
from __future__ import annotations

from typing import Dict, Optional

from paper_trading.models import Bar, Signal, SignalType
from paper_trading.strategy.base_strategy import BaseStrategy
from paper_trading.utils import get_logger

logger = get_logger(__name__)


def rsi(closes: list[float], period: int) -> Optional[float]:
    """Wilder 风格简化 RSI（等权平均涨跌幅）；数据不足返回 None。"""
    if period <= 0 or len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        diff = float(closes[i]) - float(closes[i - 1])
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


class RSIStrategy(BaseStrategy):
    """RSI 上穿超卖线买入，下穿超买线卖出（只在穿越时发信号）。"""

    params_schema = {
        "period": {"type": int, "default": 14, "min": 2, "max": 100},
        "buy_below": {"type": float, "default": 30.0, "min": 1.0, "max": 50.0},
        "sell_above": {"type": float, "default": 70.0, "min": 50.0, "max": 99.0},
        "buy_volume": {"type": int, "default": 100, "min": 100, "max": 100000},
        "sell_volume": {"type": int, "default": 100, "min": 100, "max": 100000},
    }

    def __init__(self, period: int = 14, buy_below: float = 30.0,
                 sell_above: float = 70.0, buy_volume: int = 100,
                 sell_volume: int = 100) -> None:
        super().__init__(name=f"RSI_{int(period)}")
        self.period = int(period)
        self.buy_below = float(buy_below)
        self.sell_above = float(sell_above)
        self.buy_volume = int(buy_volume)
        self.sell_volume = int(sell_volume)

    def generate_signals(self, bars: Dict[str, list[Bar]]) -> Dict[str, Signal]:
        out: Dict[str, Signal] = {}
        for symbol, rows in (bars or {}).items():
            if len(rows) < self.period + 2:
                continue
            closes = [float(b.close) for b in rows]
            prev = rsi(closes[:-1], self.period)
            curr = rsi(closes, self.period)
            if prev is None or curr is None:
                continue
            last = closes[-1]
            if prev <= self.buy_below < curr:
                out[symbol] = Signal(
                    symbol=symbol, direction=SignalType.BUY,
                    volume=self.buy_volume, price=last,
                    reason=f"RSI 上穿 {self.buy_below:g}（{prev:.1f}→{curr:.1f}）")
            elif prev >= self.sell_above > curr:
                out[symbol] = Signal(
                    symbol=symbol, direction=SignalType.SELL,
                    volume=self.sell_volume, price=last,
                    reason=f"RSI 下穿 {self.sell_above:g}（{prev:.1f}→{curr:.1f}）")
        return out
