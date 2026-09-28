"""主入口：自动化调度与结算。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.data.db_manager import DataDBManager
from paper_trading.models import Order, OrderType, Signal, TradingConfig
from paper_trading.portfolio.portfolio import Portfolio
from paper_trading.risk.risk_manager import RiskManager
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy
from paper_trading.utils import get_logger


def _get_fetcher():
    from paper_trading.data.akshare_fetcher import AkshareFetcher

    return AkshareFetcher

logger = get_logger(__name__)


class PaperTradingEngine:
    """
    模拟交易引擎。

    流程：
    1. 更新行情数据
    2. 触发策略计算信号
    3. 风控检查
    4. 提交撮合
    5. T+1 结算
    6. 记录 NAV
    """

    def __init__(
        self,
        data_db: str = "data.db",
        account_db: str = "paper_account.db",
        config: Optional[TradingConfig] = None,
    ) -> None:
        self.data_db = DataDBManager(data_db)
        self.broker = PaperBroker(account_db, config)
        self.portfolio = Portfolio(self.broker)
        self.risk = RiskManager()
        self.strategy = MACrossStrategy(short_window=5, long_window=20)
        self.config = config or TradingConfig()

    def update_data(self, symbols: list[str], full: bool = False) -> None:
        """更新行情数据 + 刷新真实股票名称。"""
        Fetcher = _get_fetcher()
        for sym in symbols:
            latest = None if full else self.data_db.get_latest_timestamp(sym)
            start = latest[:10].replace("-", "") if latest else None
            bars = Fetcher.fetch_daily(sym, start_date=start)
            if bars:
                self.data_db.upsert_bars(bars)
                self.data_db.add_stock_to_pool(sym)
        try:
            targets = sorted(set(symbols) | set(self.data_db.get_pool_symbols())
                             | {p.symbol for p in self.broker.get_all_positions()})
            fresh = Fetcher.fetch_stock_names(targets)
            if fresh:
                self.data_db.upsert_stock_names(fresh)
        except Exception as e:
            logger.warning(f"Stock name refresh skipped: {e}")

    def run_daily(self, symbols: list[str]) -> None:
        """
        执行每日结算流程。

        Args:
            symbols: 关注的股票列表
        """
        logger.info(f"=== Run-Daily started at {datetime.now().isoformat()} ===")

        # 1. 更新行情
        self.update_data(symbols)

        # 2. 获取最新K线并生成信号
        all_bars: Dict[str, list] = {}
        for sym in symbols:
            bars = self.data_db.get_bars(sym, limit=30)
            if bars:
                all_bars[sym] = bars
        # 计价用全口径（run 标的 ∪ 持仓），持仓按 0 算会误触发熔断
        latest_prices: Dict[str, float] = {}
        for sym in list(all_bars) + [p.symbol for p in self.broker.get_all_positions()]:
            bars = self.data_db.get_bars(sym, limit=1)
            if bars:
                latest_prices[sym] = bars[-1].close

        # 3. T+1 结算（先解冻昨日买入，再交易，避免 T+2）
        self.broker.unfreeze_t1()

        # 4. 策略信号
        signals = self.strategy.generate_signals(all_bars)
        logger.info(f"Generated {len(signals)} signals")

        # 5. 风控 + 撮合
        cash = self.broker.get_cash()
        positions = {p.symbol: p for p in self.broker.get_all_positions()}
        nav = self.broker.get_nav(latest_prices)
        self.risk.update_peak(nav.total_value)
        halted, dd = self.risk.check_drawdown(nav.total_value)
        if halted:
            logger.error(f"Drawdown halt: {dd:.2%} >= {self.risk.max_drawdown_pct:.2%}, skip trading")
            signals = {}

        for symbol, signal in signals.items():
            ok, reason = self.risk.check_signal(
                signal, latest_prices.get(symbol, 0.0),
                cash, positions, nav.total_value, prices=latest_prices,
            )
            if not ok:
                logger.warning(f"Signal rejected by risk: {symbol} - {reason}")
                continue

            order = Order(
                symbol=signal.symbol,
                direction=signal.direction.value,
                volume=signal.volume,
                order_type=OrderType.LIMIT,
                limit_price=signal.price,
            )
            self.broker.submit_order(order)

        # 6. 记录 NAV
        self.portfolio.record_nav(latest_prices)

        # 7. 打印摘要
        self._print_summary(latest_prices)
        logger.info("=== Run-Daily completed ===\n")

    def _print_summary(self, prices: Dict[str, float]) -> None:
        """打印账户摘要。"""
        nav = self.broker.get_nav(prices)
        positions = self.broker.get_all_positions()
        print("\n" + "=" * 60)
        print(f"  账户摘要 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
        print("=" * 60)
        print(f"  可用资金:  {nav.cash:>14,.2f}")
        print(f"  持仓市值:  {nav.market_value:>14,.2f}")
        print(f"  总资产:    {nav.total_value:>14,.2f}")
        print(f"  浮动盈亏:  {nav.pnl:>14,.2f} ({nav.pnl_pct:+.2%})")
        print("-" * 60)
        if positions:
            print("  持仓明细:")
            for p in positions:
                price = prices.get(p.symbol, 0.0)
                mv = p.total_volume * price
                print(f"    {p.symbol}: {p.total_volume}股 (可用{p.available_volume}) "
                      f"@ 成本{p.avg_cost:.2f} / 现价{price:.2f} / 市值{mv:,.0f}")
        else:
            print("  持仓明细: 无")
        print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Trading Framework")
    parser.add_argument("--symbols", nargs="+", default=["600519", "000858", "601318"],
                        help="股票代码列表")
    parser.add_argument("--data-db", default="data.db", help="行情数据库路径")
    parser.add_argument("--account-db", default="paper_account.db", help="账户数据库路径")
    parser.add_argument("--full", action="store_true", help="全量更新行情")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0,
                        help="初始资金")
    args = parser.parse_args()

    config = TradingConfig(initial_cash=args.initial_cash)
    engine = PaperTradingEngine(
        data_db=args.data_db,
        account_db=args.account_db,
        config=config,
    )

    if args.full:
        engine.update_data(args.symbols, full=True)

    engine.run_daily(args.symbols)


if __name__ == "__main__":
    main()
