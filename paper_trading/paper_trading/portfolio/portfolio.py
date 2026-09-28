"""投资组合管理。"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.models import AccountSnapshot, Position
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class Portfolio:
    """
    投资组合管理器。

    职责：
    - 汇总持仓与资金
    - 计算 NAV
    - 记录历史快照
    """

    def __init__(self, broker: PaperBroker) -> None:
        self.broker = broker

    def get_current_prices(self, symbols: list[str]) -> Dict[str, float]:
        """
        获取当前价格（从最新K线中取收盘价）。
        实际运行中由 main.py 注入最新行情。
        """
        # 由外部注入，这里仅作占位
        return {}

    def get_nav(self, current_prices: Dict[str, float]) -> AccountSnapshot:
        """获取当前 NAV 快照。"""
        return self.broker.get_nav(current_prices)

    def record_nav(self, current_prices: Dict[str, float]) -> AccountSnapshot:
        """记录 NAV 到历史。"""
        snapshot = self.get_nav(current_prices)
        self.broker.record_nav(snapshot)
        logger.info(
            f"NAV: total={snapshot.total_value:.2f}, "
            f"pnl={snapshot.pnl:.2f} ({snapshot.pnl_pct:.2%})"
        )
        return snapshot

    def get_positions_summary(self) -> list[dict]:
        """获取持仓摘要。"""
        positions = self.broker.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "total_volume": p.total_volume,
                "available_volume": p.available_volume,
                "avg_cost": p.avg_cost,
                "last_update": p.last_update.isoformat(),
            }
            for p in positions
        ]
