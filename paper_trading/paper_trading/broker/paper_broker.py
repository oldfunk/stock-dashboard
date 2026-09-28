"""本地模拟撮合引擎。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from paper_trading.models import (
    AccountSnapshot,
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Position,
    TradingConfig,
)
from paper_trading.utils import get_logger
from paper_trading.utils.trading_calendar import as_date, is_trading_day, load_holidays, next_trading_day

logger = get_logger(__name__)


class PaperBroker:
    """
    本地模拟撮合器。

    职责：
    - 管理账户资金与持仓
    - 撮合订单（支持滑点）
    - 计算交易成本（佣金/印花税/过户费）
    - T+1 持仓冻结
    - 持久化到 paper_account.db
    """

    def __init__(
        self,
        db_path: str | Path = "paper_account.db",
        config: Optional[TradingConfig] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.config = config or TradingConfig()
        self._init_schema()
        self._ensure_account()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL,
                    initial_cash REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    total_volume INTEGER NOT NULL,
                    available_volume INTEGER NOT NULL,
                    avg_cost REAL NOT NULL,
                    last_update TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction INTEGER NOT NULL,
                    volume INTEGER NOT NULL,
                    order_type TEXT NOT NULL,
                    limit_price REAL,
                    status TEXT NOT NULL,
                    filled_volume INTEGER DEFAULT 0,
                    filled_price REAL DEFAULT 0.0,
                    commission REAL DEFAULT 0.0,
                    stamp_duty REAL DEFAULT 0.0,
                    transfer_fee REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    filled_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fills (
                    fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    direction INTEGER NOT NULL,
                    volume INTEGER NOT NULL,
                    price REAL NOT NULL,
                    commission REAL NOT NULL,
                    stamp_duty REAL NOT NULL,
                    transfer_fee REAL NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nav_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    cash REAL NOT NULL,
                    market_value REAL NOT NULL,
                    total_value REAL NOT NULL,
                    available_cash REAL NOT NULL,
                    pnl REAL NOT NULL,
                    pnl_pct REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS t1_freeze (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    volume INTEGER NOT NULL,
                    freeze_date TEXT NOT NULL,
                    unfreeze_date TEXT NOT NULL,
                    is_unfrozen INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS op_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    params TEXT,
                    ok INTEGER NOT NULL,
                    result TEXT,
                    cash_after REAL,
                    total_value_after REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_date TEXT NOT NULL,
                    symbols TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    executed_at TEXT
                )
            """)

    def _ensure_account(self) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO account (id, cash, initial_cash, created_at) VALUES (1, ?, ?, ?)",
                    (self.config.initial_cash, self.config.initial_cash,
                     datetime.now().isoformat()),
                )
                logger.info(f"Account initialized with cash={self.config.initial_cash}")

    def get_cash(self) -> float:
        with self._connect() as conn:
            row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
        return float(row["cash"])

    def get_position(self, symbol: str) -> Optional[Position]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE symbol = ?", (symbol,)
            ).fetchone()
        if not row:
            return None
        return Position(
            symbol=row["symbol"],
            total_volume=row["total_volume"],
            available_volume=row["available_volume"],
            avg_cost=row["avg_cost"],
            last_update=datetime.fromisoformat(row["last_update"]),
        )

    def get_all_positions(self) -> list[Position]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()
        return [
            Position(
                symbol=r["symbol"],
                total_volume=r["total_volume"],
                available_volume=r["available_volume"],
                avg_cost=r["avg_cost"],
                last_update=datetime.fromisoformat(r["last_update"]),
            )
            for r in rows
        ]

    def _calc_buy_cost(self, amount: float) -> tuple[float, float]:
        """计算买入成本: (佣金, 过户费)。"""
        commission = max(amount * self.config.commission_rate, self.config.commission_min)
        transfer_fee = amount * self.config.transfer_fee_rate
        return commission, transfer_fee

    def _calc_sell_cost(self, amount: float) -> tuple[float, float, float]:
        """计算卖出成本: (佣金, 印花税, 过户费)。"""
        commission = max(amount * self.config.commission_rate, self.config.commission_min)
        stamp_duty = amount * self.config.stamp_duty_rate
        transfer_fee = amount * self.config.transfer_fee_rate
        return commission, stamp_duty, transfer_fee

    def _apply_slippage(self, price: float, direction: int) -> float:
        """应用滑点并按最小变动价位(0.01元)取整。direction=1买入(加价), direction=-1卖出(减价)。"""
        if self.config.use_slippage_pct:
            slippage = price * self.config.slippage_pct
        else:
            slippage = self.config.slippage_fixed
        return round(price + direction * slippage + 1e-9, 2)

    def _holidays(self) -> set[str]:
        try:
            return load_holidays(getattr(self.config, "holidays", ()))
        except Exception:
            return set()

    @staticmethod
    def limit_pct_for(symbol: str) -> float:
        """涨跌停幅度：科创688/创业300为20%，北交所8/4开头为30%，其余10%（含ST简化为10%）。"""
        if symbol.startswith("688") or symbol.startswith("300"):
            return 0.20
        if symbol.startswith("8") or symbol.startswith("4"):
            return 0.30
        return 0.10

    def _reject(self, order: Order, reason: str) -> Order:
        order.status = OrderStatus.REJECTED
        logger.warning(f"Order rejected: {reason}")
        self._persist_order(order)
        return order

    def submit_order(self, order: Order) -> Order:
        """
        提交订单并撮合。

        Returns:
            更新后的 Order 对象（拒绝单也会落库，status=REJECTED）
        """
        # A股基础校验：100股整数倍
        if order.volume <= 0 or order.volume % 100 != 0:
            return self._reject(order, f"volume must be positive multiple of 100, got {order.volume}")
        # 涨跌停检查（有 prev_close 才做）
        if order.prev_close and order.limit_price:
            pct = self.limit_pct_for(order.symbol)
            up = order.prev_close * (1 + pct)
            dn = order.prev_close * (1 - pct)
            if order.direction == 1 and order.limit_price > up + 1e-9:
                return self._reject(order, f"buy price {order.limit_price:.2f} over limit-up {up:.2f}")
            if order.direction == -1 and order.limit_price < dn - 1e-9:
                return self._reject(order, f"sell price {order.limit_price:.2f} below limit-down {dn:.2f}")
        # high/low 穿价检查（有参考bar才做，避免盘外幻影成交）
        if order.limit_price and order.ref_high and order.ref_low:
            if order.direction == 1 and order.limit_price < order.ref_low - 1e-9:
                return self._reject(order, f"buy limit {order.limit_price:.2f} below bar low {order.ref_low:.2f}")
            if order.direction == -1 and order.limit_price > order.ref_high + 1e-9:
                return self._reject(order, f"sell limit {order.limit_price:.2f} above bar high {order.ref_high:.2f}")
        # 买入: 检查资金
        if order.direction == 1:
            est_price = order.limit_price or 0.0
            if est_price <= 0:
                order.status = OrderStatus.REJECTED
                logger.warning(f"Order rejected: no limit price for market buy {order.symbol}")
                self._persist_order(order)
                return order
            exec_price = self._apply_slippage(est_price, 1)
            amount = exec_price * order.volume
            commission, transfer_fee = self._calc_buy_cost(amount)
            total_cost = amount + commission + transfer_fee
            if total_cost > self.get_cash():
                order.status = OrderStatus.REJECTED
                logger.warning(
                    f"Order rejected: insufficient cash. Need {total_cost:.2f}, have {self.get_cash():.2f}"
                )
                self._persist_order(order)
                return order

        # 卖出: 检查可用持仓
        if order.direction == -1:
            pos = self.get_position(order.symbol)
            if not pos or pos.available_volume < order.volume:
                order.status = OrderStatus.REJECTED
                logger.warning(
                    f"Order rejected: insufficient available volume for {order.symbol}. "
                    f"Need {order.volume}, have {pos.available_volume if pos else 0}"
                )
                self._persist_order(order)
                return order

        # 撮合
        exec_price = self._apply_slippage(
            order.limit_price or 0.0, order.direction
        )
        amount = exec_price * order.volume

        if order.direction == 1:
            commission, transfer_fee = self._calc_buy_cost(amount)
            stamp_duty = 0.0
            total_cost = amount + commission + transfer_fee
            self._execute_buy(order, exec_price, amount, commission, transfer_fee)
        else:
            commission, stamp_duty, transfer_fee = self._calc_sell_cost(amount)
            net_proceeds = amount - commission - stamp_duty - transfer_fee
            self._execute_sell(order, exec_price, amount, commission, stamp_duty, transfer_fee)

        order.status = OrderStatus.FILLED
        order.filled_volume = order.volume
        order.filled_price = exec_price
        order.commission = commission
        order.stamp_duty = stamp_duty
        order.transfer_fee = transfer_fee
        order.filled_at = datetime.now()

        self._persist_order(order)
        self._persist_fill(order)
        logger.info(
            f"Order filled: {'BUY' if order.direction == 1 else 'SELL'} "
            f"{order.volume} {order.symbol} @ {exec_price:.3f}"
        )
        return order

    def _execute_buy(
        self, order: Order, price: float, amount: float,
        commission: float, transfer_fee: float,
    ) -> None:
        """执行买入：扣资金、加持仓、冻结T+1。"""
        total_cost = amount + commission + transfer_fee
        with self._connect() as conn:
            conn.execute(
                "UPDATE account SET cash = cash - ? WHERE id = 1",
                (total_cost,),
            )
            # 更新持仓（avg_cost 含买入费用，与券商交割单口径一致）
            pos = self.get_position(order.symbol)
            full_cost = amount + commission + transfer_fee
            if pos:
                new_total = pos.total_volume + order.volume
                new_avg = (pos.avg_cost * pos.total_volume + full_cost) / new_total
                conn.execute(
                    """UPDATE positions SET total_volume=?, available_volume=?,
                       avg_cost=?, last_update=? WHERE symbol=?""",
                    (new_total, pos.available_volume, new_avg,
                     datetime.now().isoformat(), order.symbol),
                )
            else:
                conn.execute(
                    """INSERT INTO positions (symbol, total_volume, available_volume, avg_cost, last_update)
                       VALUES (?, ?, 0, ?, ?)""",
                    (order.symbol, order.volume, full_cost / order.volume, datetime.now().isoformat()),
                )
            # T+1 冻结：解冻日为下一交易日（跳周末/节假日）
            today = datetime.now().date()
            unfreeze = next_trading_day(today, self._holidays(), steps=1)
            conn.execute(
                """INSERT INTO t1_freeze (symbol, volume, freeze_date, unfreeze_date, is_unfrozen)
                   VALUES (?, ?, ?, ?, 0)""",
                (order.symbol, order.volume, today.isoformat(), unfreeze.isoformat()),
            )

    def _execute_sell(
        self, order: Order, price: float, amount: float,
        commission: float, stamp_duty: float, transfer_fee: float,
    ) -> None:
        """执行卖出：加资金、减持仓。"""
        net_proceeds = amount - commission - stamp_duty - transfer_fee
        with self._connect() as conn:
            conn.execute(
                "UPDATE account SET cash = cash + ? WHERE id = 1",
                (net_proceeds,),
            )
            pos = self.get_position(order.symbol)
            if pos:
                new_total = pos.total_volume - order.volume
                new_available = pos.available_volume - order.volume
                if new_total == 0:
                    conn.execute("DELETE FROM positions WHERE symbol = ?", (order.symbol,))
                else:
                    conn.execute(
                        """UPDATE positions SET total_volume=?, available_volume=?,
                           last_update=? WHERE symbol=?""",
                        (new_total, new_available, datetime.now().isoformat(), order.symbol),
                    )

    def _persist_order(self, order: Order) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO orders (symbol, direction, volume, order_type, limit_price,
                   status, filled_volume, filled_price, commission, stamp_duty, transfer_fee,
                   created_at, filled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order.symbol, order.direction, order.volume,
                 order.order_type.value, order.limit_price, order.status.value,
                 order.filled_volume, order.filled_price, order.commission,
                 order.stamp_duty, order.transfer_fee,
                 order.created_at.isoformat(),
                 order.filled_at.isoformat() if order.filled_at else None),
            )
            order.order_id = cur.lastrowid

    def _persist_fill(self, order: Order) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO fills (order_id, symbol, direction, volume, price,
                   commission, stamp_duty, transfer_fee, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order.order_id, order.symbol, order.direction, order.volume,
                 order.filled_price, order.commission, order.stamp_duty,
                 order.transfer_fee, order.filled_at.isoformat() if order.filled_at else None),
            )

    def unfreeze_t1(self, date: Optional[datetime | date] | str = None) -> int:
        """
        解冻 T+1 持仓。将指定日期之前冻结的持仓标记为可用。

        Args:
            date: 结算日期，接受 datetime/date/ISO 字符串，缺省为今天。

        Returns:
            解冻的笔数
        """
        target_date = as_date(date or datetime.now()).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM t1_freeze WHERE unfreeze_date <= ? AND is_unfrozen = 0""",
                (target_date,),
            ).fetchall()
            count = 0
            for row in rows:
                conn.execute(
                    "UPDATE positions SET available_volume = available_volume + ? WHERE symbol = ?",
                    (row["volume"], row["symbol"]),
                )
                conn.execute(
                    "UPDATE t1_freeze SET is_unfrozen = 1 WHERE id = ?",
                    (row["id"],),
                )
                count += 1
            if count > 0:
                logger.info(f"Unfroze {count} T+1 positions for {target_date}")
            return count

    def get_nav(self, current_prices: dict[str, float]) -> AccountSnapshot:
        """计算当前 NAV。"""
        cash = self.get_cash()
        positions = self.get_all_positions()
        market_value = sum(
            p.total_volume * current_prices.get(p.symbol, 0.0) for p in positions
        )
        total_value = cash + market_value
        with self._connect() as conn:
            row = conn.execute("SELECT initial_cash FROM account WHERE id = 1").fetchone()
        initial_cash = float(row["initial_cash"])
        pnl = total_value - initial_cash
        pnl_pct = pnl / initial_cash if initial_cash > 0 else 0.0
        return AccountSnapshot(
            timestamp=datetime.now(),
            cash=cash,
            market_value=market_value,
            total_value=total_value,
            available_cash=cash,
            pnl=pnl,
            pnl_pct=pnl_pct,
        )

    def record_nav(self, snapshot: AccountSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO nav_history (timestamp, cash, market_value, total_value,
                   available_cash, pnl, pnl_pct) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (snapshot.timestamp.isoformat(), snapshot.cash, snapshot.market_value,
                 snapshot.total_value, snapshot.available_cash, snapshot.pnl, snapshot.pnl_pct),
            )

    def log_operation(
        self,
        action: str,
        params: Optional[dict] = None,
        ok: bool = True,
        result: Optional[dict] = None,
        cash_after: Optional[float] = None,
        total_value_after: Optional[float] = None,
    ) -> None:
        """记录一次 AI/CLI 操作流水（供仪表盘展示）。"""
        import json as _json

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO op_log (timestamp, action, params, ok, result,
                                       cash_after, total_value_after)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), action,
                 _json.dumps(params, ensure_ascii=False, default=str) if params else None,
                 1 if ok else 0,
                 _json.dumps(result, ensure_ascii=False, default=str) if result else None,
                 cash_after, total_value_after),
            )

    def get_op_log(self, limit: int = 50) -> list[dict]:
        """读取操作流水（倒序）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM op_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def save_plan(self, plan_date: str, symbols: list[str], plan: dict) -> int:
        """存一条待执行计划（ai:plan），返回 id；同日旧 pending 自动作废。"""
        import json as _json

        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_plans SET status = 'superseded' "
                "WHERE plan_date = ? AND status = 'pending'",
                (plan_date,),
            )
            cur = conn.execute(
                """INSERT INTO agent_plans (plan_date, symbols, plan_json, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (plan_date, ",".join(symbols),
                 _json.dumps(plan, ensure_ascii=False, default=str),
                 datetime.now().isoformat()),
            )
            return int(cur.lastrowid)

    def get_pending_plan(self, plan_date: str) -> Optional[dict]:
        """取某日待执行的计划（无则 None）。"""
        import json as _json

        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM agent_plans WHERE plan_date = ? AND status = 'pending'
                   ORDER BY id DESC LIMIT 1""",
                (plan_date,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["plan"] = _json.loads(d["plan_json"])
        except Exception:
            d["plan"] = {}
        return d

    def mark_plan_done(self, plan_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_plans SET status = 'done', executed_at = ? WHERE id = ?",
                (datetime.now().isoformat(), plan_id),
            )

    def get_order_history(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY order_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_fill_history(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM fills ORDER BY fill_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
