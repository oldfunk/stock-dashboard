"""策略（MA 移植 + 价值轮动自研）。

- MACrossStrategy：移植自 paper-trading（仅最后一根交叉才发信号）。
- plan_value_rotation：自研（BaseStrategy 接口只收 bars，评分类策略走独立 planner，
  同样产出 Signal 列表，走同一条 broker 路径）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

import pandas as pd

from src.paper.types import Bar, Signal, SignalType


class BaseStrategy(ABC):
    """策略抽象基类：generate_signals(bars) -> {symbol: Signal}。"""

    def __init__(self, name: str = "base") -> None:
        self.name = name

    @abstractmethod
    def generate_signals(self, bars: Dict[str, list[Bar]]) -> Dict[str, Signal]:
        """根据行情数据生成交易信号。"""
        ...

    def on_bar(self, bars: Dict[str, list[Bar]]) -> Dict[str, Signal]:
        """每根K线触发一次。"""
        return self.generate_signals(bars)


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
            # 死叉: 前一根 MA5 >= MA20, 最后一根 MA5 < MA20
            elif prev["ma_short"] >= prev["ma_long"] and curr["ma_short"] < curr["ma_long"]:
                signals[symbol] = Signal(
                    symbol=symbol,
                    direction=SignalType.SELL,
                    volume=self.sell_volume,
                    price=curr["close"],
                    reason=f"Death cross: MA{self.short_window} crossed below MA{self.long_window}",
                )

        return signals


def plan_value_rotation(holdings: dict, ranked: list, prices: dict,
                        cash: float, total_value: float,
                        top_n: int = 10, dropout_n: int = 15,
                        budget_pct: float = 0.95) -> list:
    """价值轮动（自研）：等权持有 TopN；跌出 TopM 卖出；新进按等权买（100 股取整）。

    holdings: {code: 持有总量}；ranked: [(code, score)] 降序；
    prices: {code: 现价}；SELL 量为持有总量（调用方按可用量 clamp）。
    ranked 为空表示无评分依据，直接返回 []（不清仓）。
    """
    if not ranked:
        return []
    top_set = {c for c, _ in ranked[:top_n]}
    drop_set = {c for c, _ in ranked[:dropout_n]}
    signals = []
    for code, vol in (holdings or {}).items():
        if vol and vol > 0 and code not in drop_set:
            price = prices.get(code)
            if price:
                signals.append(Signal(symbol=code, direction=SignalType.SELL,
                                      volume=vol, price=price,
                                      reason=f"跌出 Top{dropout_n}"))
    slots = [c for c, _ in ranked[:top_n]
             if not (holdings or {}).get(c)]
    for code in slots:
        price = prices.get(code)
        if not price or price <= 0 or top_n <= 0:
            continue
        target = total_value / top_n * budget_pct
        vol = int(target // price // 100 * 100)
        if vol >= 100:
            signals.append(Signal(symbol=code, direction=SignalType.BUY,
                                  volume=vol, price=price,
                                  reason=f"新进 Top{top_n} 等权"))
    return signals
