"""双均线交叉策略。"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from paper_trading.models import Bar, Signal, SignalType
from paper_trading.strategy.base_strategy import BaseStrategy
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class MACrossStrategy(BaseStrategy):
    """
    双均线交叉策略 (MA5/MA20)。

    规则：
    - 金叉（MA5 上穿 MA20）：买入
    - 死叉（MA5 下穿 MA20）：卖出
    """

    def __init__(
        self,
        short_window: int = 5,
        long_window: int = 20,
        buy_volume: int = 100,
        sell_volume: int = 100,
    ) -> None:
        super().__init__(name=f"MA_{short_window}_{long_window}")
        self.short_window = short_window
        self.long_window = long_window
        self.buy_volume = buy_volume
        self.sell_volume = sell_volume

    def generate_signals(self, bars: Dict[str, list[Bar]]) -> Dict[str, Signal]:
        """仅当最后一根 K 线发生交叉时才发信号，避免历史金叉重复下单。"""
        signals: Dict[str, Signal] = {}
        for symbol, bar_list in bars.items():
            if len(bar_list) < self.long_window + 1:
                continue
            df = pd.DataFrame([
                {"close": b.close, "timestamp": b.timestamp} for b in bar_list
            ])
            df["ma_short"] = df["close"].rolling(self.short_window).mean()
            df["ma_long"] = df["close"].rolling(self.long_window).mean()

            prev = df.iloc[-2]
            curr = df.iloc[-1]
            if pd.isna(prev["ma_short"]) or pd.isna(prev["ma_long"]):
                continue

            # 金叉: 前一根 MA5 <= MA20, 最后一根 MA5 > MA20
            if prev["ma_short"] <= prev["ma_long"] and curr["ma_short"] > curr["ma_long"]:
                signals[symbol] = Signal(
                    symbol=symbol,
                    direction=SignalType.BUY,
                    volume=self.buy_volume,
                    price=curr["close"],
                    reason=f"Golden cross: MA{self.short_window} crossed above MA{self.long_window}",
                )
                logger.info(
                    f"Signal: BUY {symbol} @ {curr['close']:.2f} (Golden cross)"
                )

            # 死叉: 前一根 MA5 >= MA20, 最后一根 MA5 < MA20
            elif prev["ma_short"] >= prev["ma_long"] and curr["ma_short"] < curr["ma_long"]:
                signals[symbol] = Signal(
                    symbol=symbol,
                    direction=SignalType.SELL,
                    volume=self.sell_volume,
                    price=curr["close"],
                    reason=f"Death cross: MA{self.short_window} crossed below MA{self.long_window}",
                )
                logger.info(
                    f"Signal: SELL {symbol} @ {curr['close']:.2f} (Death cross)"
                )

        return signals
