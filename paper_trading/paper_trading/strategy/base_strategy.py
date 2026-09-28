"""策略抽象基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

from paper_trading.models import Bar, Signal


class BaseStrategy(ABC):
    """
    策略抽象基类。

    子类需实现 `generate_signals` 方法，接收当前行情数据，
    返回标准交易信号字典 {symbol: Signal}。
    """

    def __init__(self, name: str = "base") -> None:
        self.name = name

    @abstractmethod
    def generate_signals(self, bars: Dict[str, list[Bar]]) -> Dict[str, Signal]:
        """
        根据行情数据生成交易信号。

        Args:
            bars: {symbol: [Bar, ...]} 各股票的历史K线

        Returns:
            {symbol: Signal} 交易信号字典
        """
        ...

    def on_bar(self, bars: Dict[str, list[Bar]]) -> Dict[str, Signal]:
        """每根K线触发一次。"""
        return self.generate_signals(bars)
