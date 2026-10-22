"""风控模块。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterator, Optional

from paper_trading.models import Order, Position, Signal, TradingConfig
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class RiskState:
    """风控状态持久层（与账本同库的 risk_state 表）。

    为什么落库：峰值与当日亏损基线若只在内存，守护进程重启就归零，
    熔断形同虚设（审计 P0）。所有模块（CLI/引擎/条件单/AI）共用同一份。
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, key: str, default=None):
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM risk_state WHERE key = ?",
                               (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO risk_state (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (key, str(value), datetime.now().isoformat()))


class RiskManager:
    """
    风控管理器。

    职责：
    - 单笔订单金额上限
    - 单只股票持仓上限
    - 总仓位上限
    - 最大回撤止损（峰值可落库）
    - 当日亏损熔断（日亏基线可落库）
    """

    def __init__(
        self,
        max_single_order_value: float = 200_000.0,
        max_position_pct: float = 0.3,       # 单只股票最多30%仓位
        max_total_position_pct: float = 0.95,  # 总仓位不超过95%
        max_drawdown_pct: float = 0.20,      # 最大回撤20%止损
        daily_loss_halt_pct: float = 0.05,   # 当日亏损（自日初基线）熔断
        db_path: Optional[str | Path] = None,  # 给定时峰值/日亏基线落库
    ) -> None:
        self.max_single_order_value = max_single_order_value
        self.max_position_pct = max_position_pct
        self.max_total_position_pct = max_total_position_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_halt_pct = float(daily_loss_halt_pct or 0.0)
        self._state = RiskState(db_path) if db_path else None
        # 兼容旧调用：显式传 db_path 时优先读落库峰值
        self._peak_value: float = 0.0
        if self._state is not None:
            try:
                self._peak_value = float(self._state.get("peak_total_value") or 0.0)
            except (TypeError, ValueError):
                self._peak_value = 0.0
        self._daily_baselines: Dict[str, float] = {}  # 无 db 时的内存兜底

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

        # 1. 单笔金额上限（只约束买入；卖出是出清/止损，不能被金额上限卡住）
        if signal.direction.value == 1 and order_value > self.max_single_order_value:
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
        """更新峰值净值（有 db_path 时落库，重启不丢）。"""
        if total_value > self._peak_value:
            self._peak_value = total_value
            if self._state is not None:
                self._state.set("peak_total_value", f"{total_value:.4f}")

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

    def check_daily_loss(self, total_value: float, day: Optional[str] = None,
                         halt_pct: Optional[float] = None) -> tuple[bool, float]:
        """检查当日亏损熔断：以当日第一次观测到的总资产为基线。

        Returns:
            (是否触发, 当日亏损比例)。亏损为正数，盈利为负数。
        基线在当日首次调用时写入 risk_state（重启不丢；引擎开盘首轮即可锚定）。
        """
        if total_value <= 0:
            return False, 0.0
        day = day or date.today().isoformat()
        key = f"daily_baseline:{day}"
        baseline = self._daily_baselines.get(key)
        if baseline is None and self._state is not None:
            try:
                baseline = float(self._state.get(key) or 0.0)
            except (TypeError, ValueError):
                baseline = 0.0
        if not baseline or baseline <= 0:
            baseline = float(total_value)
            self._daily_baselines[key] = baseline
            if self._state is not None:
                self._state.set(key, f"{baseline:.4f}")
        loss = (baseline - total_value) / baseline
        limit = float(self.daily_loss_halt_pct if halt_pct is None else halt_pct)
        return (limit > 0 and loss >= limit), loss
