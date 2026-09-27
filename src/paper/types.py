"""纸盘核心数据类型（移植自 paper-trading，行为一致）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class SignalType(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0


@dataclass
class Bar:
    """单根K线数据。"""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    turn: float = 0.0


@dataclass
class Order:
    """挂单。"""
    symbol: str
    direction: int  # 1=买入, -1=卖出
    volume: int
    order_type: OrderType
    limit_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_volume: int = 0
    filled_price: float = 0.0
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    order_id: Optional[int] = None
    prev_close: Optional[float] = None
    ref_high: Optional[float] = None
    ref_low: Optional[float] = None


@dataclass
class Fill:
    """成交记录。"""
    order_id: int
    symbol: str
    direction: int
    volume: int
    price: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    timestamp: datetime = field(default_factory=datetime.now)
    fill_id: Optional[int] = None


@dataclass
class Position:
    """持仓。"""
    symbol: str
    total_volume: int
    available_volume: int  # T+1: 当天买入当天不可用
    avg_cost: float
    last_update: datetime


@dataclass
class Signal:
    """策略信号。"""
    symbol: str
    direction: SignalType
    volume: int
    price: Optional[float] = None
    reason: str = ""


@dataclass
class AccountSnapshot:
    """账户快照（NAV历史）。"""
    timestamp: datetime
    cash: float
    market_value: float
    total_value: float
    available_cash: float
    pnl: float
    pnl_pct: float


@dataclass
class TradingConfig:
    """交易配置（费率/滑点口径与子项目一致；初始资金 10 万经用户拍板）。"""
    commission_rate: float = 0.00025
    commission_min: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_fixed: float = 0.01
    slippage_pct: float = 0.001
    use_slippage_pct: bool = False
    initial_cash: float = 100_000.0
    holidays: tuple = ()
