"""Paper Trading 命令行入口（CLI + JSON 输出）。

使用方式：
    # 状态与结算
    python -m paper_trading.cli status
    python -m paper_trading.cli run --symbols 600519
    python -m paper_trading.cli buy --symbol 600519 --volume 100
    python -m paper_trading.cli sell --symbol 600519 --volume 100
    python -m paper_trading.cli nav
    python -m paper_trading.cli history --limit 20

    # 定时任务（系统 cron 调用）
    python -m paper_trading.cli cron-run --symbols 600519 000858

    # 机器可读输出（供外部 agent/脚本解析）
    python -m paper_trading.cli status --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 确保项目根目录（paper_trading 包的上级）在 sys.path 中，兼容直接脚本运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.data.db_manager import DataDBManager
from paper_trading.models import Order, OrderType, TradingConfig
from paper_trading.portfolio.portfolio import Portfolio
from paper_trading.risk.risk_manager import RiskManager
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy
from paper_trading.utils import get_logger


def _get_fetcher():
    from paper_trading.data.akshare_fetcher import AkshareFetcher

    return AkshareFetcher

logger = get_logger(__name__)


class TradingBridge:
    """
    交易编排层（应用总线）。

    职责：
    - 封装交易框架的启动、运行、查询操作
    - 提供 CLI 接口供系统 cron / 外部 agent 调用
    - 提供 JSON 输出供机器解析
    """

    def __init__(
        self,
        data_db: str = "data.db",
        account_db: str = "paper_account.db",
        config: Optional[TradingConfig] = None,
        config_path: Optional[str] = None,
        secrets_path: Optional[str] = None,
        scheme: Optional[str] = None,
    ) -> None:
        # config.yaml 单一真相源（显式 TradingConfig 优先，其次 config_path，其次默认 config.yaml）
        risk_kwargs: dict = {}
        strat_kwargs: dict = {"short_window": 5, "long_window": 20}
        stock_pool: list[str] = []
        agent_kwargs: dict = {}
        if config is None:
            try:
                from paper_trading.utils.config import load_config

                cfg = load_config(config_path or "config.yaml")
                if cfg.get("raw"):
                    config = cfg["trading_config"]
                    risk_kwargs = cfg.get("risk", {})
                    strat_kwargs = cfg.get("strategy", {})
                    stock_pool = [str(s) for s in cfg.get("stock_pool", [])]
                    agent_kwargs = cfg.get("agent", {})
            except Exception:
                config = TradingConfig()
        from pathlib import Path as _Path

        from paper_trading.strategy.schemes import active_name as _active_name
        from paper_trading.strategy.schemes import resolve_scheme as _resolve_scheme

        _root = _Path(__file__).resolve().parents[1]
        _cfg_active = str(strat_kwargs.get("active", "general") or "general")
        if scheme:
            _scheme_name, _scheme_src = scheme, "cli"
        else:
            _scheme_name, _scheme_src = _active_name(_root, _cfg_active)
            if _scheme_src == "default":
                _scheme_src = "config"
        self.scheme = _resolve_scheme(_scheme_name, _root)
        self.scheme_source = _scheme_src
        if self.scheme.name != _scheme_name:
            # 未知名或母策略不可用时回退默认，如实标记（cron 无人值守时可审计）
            self.scheme_source = f"{_scheme_src}(回退:{_scheme_name})"
        # 方案参数覆盖同名配置（方案没写的沿用 config；注意 agent 类归 agent_kwargs）
        _sig = self.scheme.signal or {}
        for _k in ("short_window", "long_window", "buy_volume", "sell_volume"):
            if _k in _sig:
                try:
                    strat_kwargs[_k] = int(_sig[_k])
                except (TypeError, ValueError):
                    pass
        for _k, _cast in (("max_orders_per_run", int), ("max_order_value", float),
                          ("daily_loss_halt_pct", float)):
            for _src in (self.scheme.sizing or {}, self.scheme.risk or {}):
                if _k in _src:
                    try:
                        agent_kwargs[_k] = _cast(_src[_k])
                    except (TypeError, ValueError):
                        pass
        self.data_db = DataDBManager(data_db)
        self.broker = PaperBroker(account_db, config)
        self.portfolio = Portfolio(self.broker)
        self.risk = RiskManager(**risk_kwargs) if risk_kwargs else RiskManager()
        self.strategy = MACrossStrategy(
            short_window=int(strat_kwargs.get("short_window", 5)),
            long_window=int(strat_kwargs.get("long_window", 20)),
            buy_volume=int(strat_kwargs.get("buy_volume", 100)),
            sell_volume=int(strat_kwargs.get("sell_volume", 100)),
        )
        self.config = config or TradingConfig()
        self.stock_pool = stock_pool
        self.secrets_path = secrets_path
        from paper_trading.agent import AgentConfig

        self.agent_cfg = AgentConfig(
            enabled=bool(agent_kwargs.get("enabled", True)),
            max_orders_per_run=int(agent_kwargs.get("max_orders_per_run", 3)),
            max_order_value=float(agent_kwargs.get("max_order_value", 20000.0)),
            daily_loss_halt_pct=float(agent_kwargs.get("daily_loss_halt_pct", 0.05)),
            llm_timeout=float(agent_kwargs.get("llm_timeout", 120.0)),
            llm_retries=int(agent_kwargs.get("llm_retries", 1)),
            llm_max_tokens=int(agent_kwargs.get("llm_max_tokens", 8192)),
            llm_thinking=str(agent_kwargs.get("llm_thinking", "disabled")),
        )

    def sync_data(self, symbols: list[str]) -> dict:
        """只同步行情+名称，不发信号、不下单、不记 NAV（供日内补数/定时任务用）。"""
        try:
            Fetcher = _get_fetcher()
        except Exception as e:
            logger.error(f"Fetcher unavailable (offline mode): {e}")
            return {"ok": False, "symbols": symbols, "updated": {},
                    "error": "akshare unavailable"}
        updated: dict[str, int] = {}
        for sym in symbols:
            try:
                latest = self.data_db.get_latest_timestamp(sym)
                start = latest[:10].replace("-", "") if latest else None
                bars = Fetcher.fetch_daily(sym, start_date=start)
                if bars:
                    self.data_db.upsert_bars(bars)
                    self.data_db.add_stock_to_pool(sym)
                updated[sym] = len(bars) if bars else 0
            except Exception as e:
                logger.error(f"Failed to sync {sym}: {e}")
                updated[sym] = -1
        try:
            targets = sorted(set(symbols) | set(self.stock_pool)
                             | set(self.data_db.get_pool_symbols())
                             | {p.symbol for p in self.broker.get_all_positions()})
            fresh = Fetcher.fetch_stock_names(targets)
            if fresh:
                self.data_db.upsert_stock_names(fresh)
        except Exception as e:
            logger.warning(f"Stock name refresh skipped: {e}")
        ok = all(v >= 0 for v in updated.values())
        return {"ok": ok, "symbols": symbols, "updated": updated}

    def price_map(self, symbols: list[str]) -> dict[str, float]:
        """最新价表：run 标的 ∪ 全部持仓（持仓不在池中时也必须计价，
        否则 NAV 把持仓算成 0 并误触发熔断）。"""
        out: dict[str, float] = {}
        for sym in symbols:
            bars = self.data_db.get_bars(sym, limit=1)
            if bars:
                out[sym] = bars[-1].close
        for p in self.broker.get_all_positions():
            if p.symbol not in out:
                bars = self.data_db.get_bars(p.symbol, limit=1)
                if bars:
                    out[p.symbol] = bars[-1].close
        return out

    def run_daily(self, symbols: list[str]) -> dict:
        """
        执行每日结算流程（Run-Daily）。

        Args:
            symbols: 关注的股票列表

        Returns:
            执行结果摘要
        """
        logger.info(f"=== TradingBridge Run-Daily started at {datetime.now().isoformat()} ===")

        # 1. 更新行情 + 名称（复用 sync，与 sync 命令同一口径）
        self.sync_data(symbols)

        # 2. 获取最新K线并生成信号
        all_bars: dict[str, list] = {}
        for sym in symbols:
            bars = self.data_db.get_bars(sym, limit=30)
            if bars:
                all_bars[sym] = bars
        # 计价用全口径（run 标的 ∪ 持仓），持仓按 0 算会误触发熔断
        latest_prices = self.price_map(symbols)

        # 2.5 无新鲜数据守卫：节假日/拉取失败时不交易只记 NAV，
        #     避免用停滞的末根 K 线重复触发历史交叉信号
        today = datetime.now().date()
        fresh = any(bars and bars[-1].timestamp.date() >= today
                    for bars in all_bars.values())
        if not fresh:
            logger.warning("No fresh bars today, skip signals+unfreeze, record NAV only")
            self.portfolio.record_nav(latest_prices)
            nav0 = self.broker.get_nav(latest_prices)
            self.broker.log_operation(
                "run", {"symbols": symbols, "skipped": "no-fresh-bars"}, True,
                {"signals": 0, "orders": 0}, nav0.cash, nav0.total_value)
            return {
                "timestamp": datetime.now().isoformat(),
                "symbols": symbols,
                "signals_generated": 0,
                "orders_executed": 0,
                "orders": [],
                "skipped": "no-fresh-bars",
                "nav": self.broker.get_nav(latest_prices).__dict__,
                "positions": [
                    {"symbol": p.symbol, "total_volume": p.total_volume,
                     "available_volume": p.available_volume, "avg_cost": p.avg_cost}
                    for p in self.broker.get_all_positions()
                ],
            }

        # 3. T+1 结算（先解冻再交易）
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
            logger.error(f"Drawdown halt: {dd:.2%}, skip trading")
            signals = {}

        executed_orders = []
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
            result = self.broker.submit_order(order)
            executed_orders.append({
                "symbol": symbol,
                "direction": "BUY" if signal.direction.value == 1 else "SELL",
                "volume": signal.volume,
                "price": result.filled_price,
                "status": result.status.value,
            })

        # 6. 记录 NAV
        self.portfolio.record_nav(latest_prices)

        # 7. 构建结果
        result = {
            "timestamp": datetime.now().isoformat(),
            "symbols": symbols,
            "signals_generated": len(signals),
            "orders_executed": len(executed_orders),
            "orders": executed_orders,
            "nav": self.broker.get_nav(latest_prices).__dict__,
            "positions": [
                {
                    "symbol": p.symbol,
                    "total_volume": p.total_volume,
                    "available_volume": p.available_volume,
                    "avg_cost": p.avg_cost,
                }
                for p in self.broker.get_all_positions()
            ],
        }

        logger.info("=== TradingBridge Run-Daily completed ===")
        return result

    def get_status(self) -> dict:
        """获取账户状态（含行情新鲜度 data_asof）。"""
        positions = self.broker.get_all_positions()
        cash = self.broker.get_cash()

        # 获取最新价格
        latest_prices = {}
        for p in positions:
            bars = self.data_db.get_bars(p.symbol, limit=1)
            if bars:
                latest_prices[p.symbol] = bars[-1].close

        nav = self.broker.get_nav(latest_prices)

        # 行情新鲜度：全库最新 K 线日期（让你一眼看到数据到哪天）
        try:
            data_asof = self.data_db.get_data_asof()
        except Exception:
            data_asof = None

        return {
            "timestamp": datetime.now().isoformat(),
            "data_asof": data_asof,
            "cash": cash,
            "market_value": nav.market_value,
            "total_value": nav.total_value,
            "pnl": nav.pnl,
            "pnl_pct": nav.pnl_pct,
            "positions": [
                {
                    "symbol": p.symbol,
                    "total_volume": p.total_volume,
                    "available_volume": p.available_volume,
                    "avg_cost": p.avg_cost,
                    "current_price": latest_prices.get(p.symbol, 0.0),
                    "market_value": p.total_volume * latest_prices.get(p.symbol, 0.0),
                }
                for p in positions
            ],
        }

    def place_order(
        self,
        symbol: str,
        direction: str,
        volume: int,
        price: Optional[float] = None,
    ) -> dict:
        """
        手动下单（强制走 RiskManager，与策略信号同等风控）。
        """
        if direction not in ("buy", "sell"):
            return {"ok": False, "error": f"Invalid direction {direction}, expect buy/sell"}
        if volume <= 0 or volume % 100 != 0:
            return {"ok": False, "error": f"Volume must be positive multiple of 100, got {volume}"}
        if price is None:
            bars = self.data_db.get_bars(symbol, limit=1)
            if not bars:
                return {"ok": False, "error": f"No data for {symbol}"}
            price = bars[-1].close

        # 名称缓存缺失时尝试补齐（真实名称，失败不阻断下单）
        if symbol not in self.data_db.get_stock_names():
            try:
                fresh = _get_fetcher().fetch_stock_names([symbol])
                if fresh:
                    self.data_db.upsert_stock_names(fresh)
            except Exception:
                pass

        from paper_trading.models import Signal, SignalType
        dir_value = 1 if direction == "buy" else -1
        signal = Signal(
            symbol=symbol,
            direction=SignalType.BUY if dir_value == 1 else SignalType.SELL,
            volume=volume,
            price=price,
            reason="manual",
        )
        # 风控前置
        positions = {p.symbol: p for p in self.broker.get_all_positions()}
        cash = self.broker.get_cash()
        latest = {symbol: price}
        for p in positions:
            b = self.data_db.get_bars(p, limit=1)
            if b:
                latest[p] = b[-1].close
        nav = self.broker.get_nav(latest)
        self.risk.update_peak(nav.total_value)
        halted, dd = self.risk.check_drawdown(nav.total_value)
        if halted:
            return {"ok": False, "error": f"Drawdown halt {dd:.2%}, order blocked"}
        ok, reason = self.risk.check_signal(signal, price, cash, positions, nav.total_value)
        if not ok:
            return {"ok": False, "error": f"Risk rejected: {reason}"}

        order = Order(
            symbol=symbol,
            direction=dir_value,
            volume=volume,
            order_type=OrderType.LIMIT,
            limit_price=price,
        )
        result = self.broker.submit_order(order)

        return {
            "ok": result.status.value == "filled",
            "order_id": result.order_id,
            "symbol": symbol,
            "direction": direction,
            "volume": volume,
            "price": result.filled_price,
            "status": result.status.value,
            "commission": result.commission,
            "stamp_duty": result.stamp_duty,
            "transfer_fee": result.transfer_fee,
        }

    def preview_order(
        self, symbol: str, direction: str, volume: int, price: Optional[float] = None
    ) -> dict:
        """Dry-run：只做风控+费用试算，不落库（供 agent 下单前调用）。"""
        if direction not in ("buy", "sell"):
            return {"ok": False, "error": "direction must be buy/sell"}
        if price is None:
            bars = self.data_db.get_bars(symbol, limit=1)
            if not bars:
                return {"ok": False, "error": f"No data for {symbol}"}
            price = bars[-1].close
        from paper_trading.models import Signal, SignalType

        signal = Signal(
            symbol=symbol,
            direction=SignalType.BUY if direction == "buy" else SignalType.SELL,
            volume=volume,
            price=price,
            reason="preview",
        )
        positions = {p.symbol: p for p in self.broker.get_all_positions()}
        cash = self.broker.get_cash()
        latest = {symbol: price}
        for p in positions:
            b = self.data_db.get_bars(p, limit=1)
            if b:
                latest[p] = b[-1].close
        nav = self.broker.get_nav(latest)
        ok, reason = self.risk.check_signal(signal, price, cash, positions, nav.total_value, prices=latest)
        amount = price * volume
        if direction == "buy":
            commission, transfer = self.broker._calc_buy_cost(amount)
            return {"ok": ok, "error": None if ok else f"Risk rejected: {reason}",
                    "estimate": {"amount": amount, "commission": commission,
                                 "transfer_fee": transfer, "total_cost": amount + commission + transfer}}
        commission, stamp, transfer = self.broker._calc_sell_cost(amount)
        return {"ok": ok, "error": None if ok else f"Risk rejected: {reason}",
                "estimate": {"amount": amount, "commission": commission,
                             "stamp_duty": stamp, "transfer_fee": transfer,
                             "net_proceeds": amount - commission - stamp - transfer}}

    def get_nav_history(self, limit: int = 30) -> list[dict]:
        """获取 NAV 历史。"""
        with self.broker._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM nav_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_order_history(self, limit: int = 20) -> list[dict]:
        """获取订单历史。"""
        return self.broker.get_order_history(limit)

    def get_fill_history(self, limit: int = 20) -> list[dict]:
        """获取成交历史。"""
        return self.broker.get_fill_history(limit)

    # ---------- LLM（P1：只咨询，不交易） ----------

    def llm_status(self) -> dict:
        """LLM 配置状态（安全视图，无 Key 原文）。"""
        from paper_trading.llm import public_provider_view

        return public_provider_view(self.secrets_path)

    def llm_save(self, preset: str = "custom", base_url: str = "",
                 model: str = "", api_key: str = "") -> dict:
        """保存厂商配置（合并语义：Key/地址留空=沿用已存值，不会被洗掉）。"""
        from paper_trading.llm import (
            load_secrets as _load, preset_base_url, public_provider_view, save_provider,
        )

        existing = (_load(self.secrets_path).get("provider") or {})
        base_url = base_url.strip() or preset_base_url(preset) \
            or str(existing.get("base_url", ""))
        if not base_url:
            return {"ok": False, "error": "未知厂商且未填写接口地址（base_url）"}
        api_key = api_key.strip() or str(existing.get("api_key", ""))
        if not api_key:
            return {"ok": False, "error": "请填写 API Key（首次配置必填；之后换模型不用重填）"}
        save_provider({"preset": preset, "base_url": base_url,
                       "model": model.strip(), "api_key": api_key},
                      self.secrets_path)
        view = public_provider_view(self.secrets_path)
        self.broker.log_operation("llm:config", {"preset": preset, "model": view["model"]},
                                  True, {"base_url": base_url}, None, None)
        # 口令仅在此处返回一次（落盘+本次回显），日志与常规回显均不含它
        return {"ok": True, "admin_token": _load(self.secrets_path).get("admin_token", ""),
                **view}

    def _llm_config(self):
        from paper_trading.llm import LLMConfig, LLMError, load_secrets

        data = load_secrets(self.secrets_path)
        p = data.get("provider") or {}
        if not p.get("base_url") or not p.get("api_key"):
            raise LLMError("LLM 未配置（先在面板或 llm-config 填写接口地址与 Key）")
        return LLMConfig(base_url=p["base_url"], api_key=p["api_key"],
                         model=p.get("model", ""))

    def llm_models(self) -> dict:
        """拉取远端模型列表（Key 不出本机，只返回 id）。"""
        from paper_trading.llm import LLMError, list_models

        try:
            models = list_models(self._llm_config())
            return {"ok": True, "models": models}
        except LLMError as e:
            return {"ok": False, "error": str(e)}

    def llm_context(self, max_ops: int = 10, max_nav: int = 5,
                    closes_n: int = 5,
                    symbols: list[str] | None = None) -> dict:
        """组装项目实时快照（给 LLM 当上下文；不含 Key/口令等任何密钥）。

        合并模式下自动附加 Stock Dashboard 的价值视角（基本面/AI 分析/论点/大盘），
        母库不可读时该段为空，绝不影响主流程。
        symbols 传入时并入覆盖范围（决策上下文与选股范围一致）。
        """
        st = self.get_status()
        names = self.data_db.get_stock_names()

        def _nm(sym: str) -> str:
            return f"{names[sym]}({sym})" if names.get(sym) else sym

        symbols = sorted(
            {p["symbol"] for p in st["positions"]}
            | set(self.data_db.get_pool_symbols())
            | set(self.stock_pool)
            | set(symbols or []))
        closes: dict[str, list] = {}
        for sym in symbols:
            bars = self.data_db.get_bars(sym, limit=closes_n)
            if bars:
                closes[_nm(sym)] = [
                    {"date": b.timestamp.date().isoformat(), "close": b.close}
                    for b in bars]

        nav_hist = self.get_nav_history(limit=max_nav)
        ops = self.broker.get_op_log(limit=max_ops)
        out: dict = {
            "account": {
                "cash": st["cash"], "total_value": st["total_value"],
                "market_value": st["market_value"], "pnl": st["pnl"],
                "pnl_pct": st["pnl_pct"], "data_asof": st.get("data_asof"),
                "currency": "CNY",
            },
            "positions": [
                {"stock": _nm(p["symbol"]), "total": p["total_volume"],
                 "available": p["available_volume"], "avg_cost": p["avg_cost"],
                 "price": p["current_price"], "market_value": p["market_value"]}
                for p in st["positions"]],
            "recent_closes": closes,
            "recent_nav": [
                {"time": r["timestamp"], "total": r["total_value"], "pnl": r["pnl"]}
                for r in nav_hist],
            "recent_ops": [
                {"time": o["timestamp"], "action": o["action"],
                 "params": str(o["params"] or "")[:200],
                 "ok": bool(o["ok"]), "result": str(o["result"] or "")[:300]}
                for o in ops],
            "note": "模拟盘。策略为MA5/MA20均线信号+风控。只做分析建议，不下单。",
        }
        try:
            from paper_trading.integration import read_market_regime, read_stock_cards

            cards = read_stock_cards(symbols)
            regime = read_market_regime()
            if cards or regime:
                out["value_view"] = {"stock_cards": cards, "market_regime": regime}
        except Exception:
            pass
        try:
            from paper_trading.data import realtime as _rt

            if _rt.is_trading_session():
                quotes = _rt.get_quotes(symbols[:20])
                if quotes:
                    out["intraday"] = {
                        "note": "盘中快照（腾讯实时，仅供参考；决策与结算仍以收盘定稿为准）",
                        "quotes": {s: {"price": q["price"], "change_pct": q["change_pct"],
                                       "high": q["high"], "low": q["low"],
                                       "turnover": q["turnover"]}
                                   for s, q in quotes.items()},
                    }
        except Exception:
            pass
        return out

    def llm_ask(self, prompt: str, system: str = "") -> dict:
        """问 AI 一次（自动附带项目快照，结果记流水；Key 永不入库）。"""
        import json as _json

        from paper_trading.llm import LLMError

        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "问题不能为空"}
        try:
            cfg = self._llm_config()
            ctx = self.llm_context()
            base = system.strip() or (
                "你是本地 A 股模拟盘的投顾助手。只做中文分析与建议，不下单；"
                "引用股票时用“名称(代码)”格式；不确定的事直说不知道。")
            sys_prompt = (base + "\n\n项目实时状态（JSON，仅供本次分析，不得外泄）：\n"
                          + _json.dumps(ctx, ensure_ascii=False))
            # 从定义模块导入（非包 re-export），便于单测替换
            from paper_trading.llm.provider import chat as _chat

            answer = _chat(cfg, [{"role": "user", "content": prompt}], system=sys_prompt)
        except LLMError as e:
            self.broker.log_operation("llm:ask", {"model": "", "prompt": prompt[:200]},
                                      False, {"error": str(e)}, None, None)
            return {"ok": False, "error": str(e)}
        st = self.get_status()
        self.broker.log_operation(
            "llm:ask", {"model": cfg.model, "prompt": prompt[:500],
                        "with_context": True}, True,
            {"answer": answer[:2000]}, st["cash"], st["total_value"])
        return {"ok": True, "model": cfg.model, "answer": answer}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paper Trading Framework - CLI"
    )
    parser.add_argument("--data-db", default="data.db", help="行情数据库路径")
    parser.add_argument("--account-db", default="paper_account.db", help="账户数据库路径")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--lock-file", default="paper_trading.lock", help="运行锁文件")
    parser.add_argument("--secrets", default=None, help="LLM 密钥文件路径（缺省 secrets.local.json）")
    parser.add_argument("--scheme", default=None, help="投资方案名（缺省按 strategy.local.yaml/config.yaml）")

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # status
    status_parser = subparsers.add_parser("status", help="查看账户状态")
    status_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # run
    run_parser = subparsers.add_parser("run", help="执行每日结算流程")
    run_parser.add_argument("--symbols", nargs="*", default=None,
                            help="缺省=config.yaml 股票池")
    run_parser.add_argument("--pool-from", default="config",
                            choices=["config", "watchlist", "screening", "all"],
                            help="股票池来源（watchlist/screening 需 Stock Dashboard 在同一台机器）")
    run_parser.add_argument("--pool-tag", default="",
                            help="screening 按母策略 tag 过滤（如 growth；为空不过滤）")
    run_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    run_parser.add_argument("--dry-run", action="store_true", help="只生成信号与风控预览，不下单")

    # sync
    sync_parser = subparsers.add_parser("sync", help="只同步行情+名称，不交易（可日内执行）")
    sync_parser.add_argument("--symbols", nargs="*", default=None,
                             help="缺省=config.yaml 股票池")
    sync_parser.add_argument("--pool-from", default="config",
                             choices=["config", "watchlist", "screening", "all"])
    sync_parser.add_argument("--pool-tag", default="")
    sync_parser.add_argument("--json", action="store_true")

    # buy
    buy_parser = subparsers.add_parser("buy", help="买入")
    buy_parser.add_argument("--symbol", required=True, help="股票代码")
    buy_parser.add_argument("--volume", type=int, required=True, help="数量")
    buy_parser.add_argument("--price", type=float, default=None, help="限价")
    buy_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # sell
    sell_parser = subparsers.add_parser("sell", help="卖出")
    sell_parser.add_argument("--symbol", required=True, help="股票代码")
    sell_parser.add_argument("--volume", type=int, required=True, help="数量")
    sell_parser.add_argument("--price", type=float, default=None, help="限价")
    sell_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # names
    names_parser = subparsers.add_parser("names", help="刷新并显示真实股票名称")
    names_parser.add_argument("--symbols", nargs="*", default=None,
                              help="缺省=股票池+持仓")
    names_parser.add_argument("--json", action="store_true")

    # preview
    preview_parser = subparsers.add_parser("preview", help="下单前风控+费用试算（不落库）")
    preview_parser.add_argument("--symbol", required=True)
    preview_parser.add_argument("--direction", choices=["buy", "sell"], required=True)
    preview_parser.add_argument("--volume", type=int, required=True)
    preview_parser.add_argument("--price", type=float, default=None)
    preview_parser.add_argument("--json", action="store_true")

    # scheme
    scheme_parser = subparsers.add_parser("scheme", help="投资方案（打法）查看与切换")
    scheme_parser.add_argument("--json", action="store_true")
    scheme_sub = scheme_parser.add_subparsers(dest="scheme_command")
    scheme_list_p = scheme_sub.add_parser("list", help="列出三类方案与当前选中")
    scheme_list_p.add_argument("--json", action="store_true")
    scheme_use = scheme_sub.add_parser("use", help="切换当前方案（写本地文件，不进 git）")
    scheme_use.add_argument("--name", required=True)
    scheme_use.add_argument("--instruction", default=None,
                            help="自定义指令（仅 custom 方案，自然语言）")
    scheme_use.add_argument("--json", action="store_true")

    # llm
    llm_parser = subparsers.add_parser("llm", help="LLM 接入（只咨询，不交易）")
    llm_parser.add_argument("--json", action="store_true")
    llm_sub = llm_parser.add_subparsers(dest="llm_command")

    llm_cfg = llm_sub.add_parser("config", help="保存厂商配置（Key 只落本地文件）")
    llm_cfg.add_argument("--preset", default="custom",
                         help="deepseek/qwen/moonshot/glm/doubao/openai/custom")
    llm_cfg.add_argument("--base-url", default="")
    llm_cfg.add_argument("--model", default="")
    llm_cfg.add_argument("--api-key", default="")
    llm_cfg.add_argument("--show", action="store_true", help="只显示当前配置（掩码）")
    llm_cfg.add_argument("--json", action="store_true")

    llm_models_p = llm_sub.add_parser("models", help="拉取远端模型列表")
    llm_models_p.add_argument("--json", action="store_true")
    llm_ask = llm_sub.add_parser("ask", help="问 AI 一次（记流水）")
    llm_ask.add_argument("--prompt", required=True)
    llm_ask.add_argument("--system", default="")
    llm_ask.add_argument("--json", action="store_true")

    # agent
    agent_parser = subparsers.add_parser("agent", help="AI 交易员（按定时触发决策，可自动下单）")
    agent_parser.add_argument("--json", action="store_true")
    agent_sub = agent_parser.add_subparsers(dest="agent_command")
    agent_run = agent_sub.add_parser("run", help="执行一次 AI 决策（默认走配置池）")
    agent_run.add_argument("--symbols", nargs="*", default=None)
    agent_run.add_argument("--pool-from", default="config",
                           choices=["config", "watchlist", "screening", "all"],
                           help="AI 选股范围（screening=Stock Dashboard 最新候选）")
    agent_run.add_argument("--pool-tag", default="",
                           help="screening 按母策略 tag 过滤；空则用当前方案自带 tag")
    agent_run.add_argument("--pool-limit", type=int, default=20)
    agent_run.add_argument("--json", action="store_true")
    agent_run.add_argument("--dry-run", action="store_true", help="只决策不下单")
    agent_run.add_argument("--force", action="store_true", help="忽略今日已决策闸")
    agent_run.add_argument("--ignore-schedule", action="store_true",
                           help="忽略定时闸（时段/次数限制），立即执行")
    agent_run.add_argument("--plan-only", action="store_true",
                           help="休盘做计划存着，开盘执行（不碰账本）")
    agent_tick = agent_sub.add_parser("tick", help="队列 tick：消费最早已到未跑的条目并执行")
    agent_tick.add_argument("--symbols", nargs="*", default=None)
    agent_tick.add_argument("--pool-from", default="config",
                            choices=["config", "watchlist", "screening", "all"])
    agent_tick.add_argument("--pool-tag", default="")
    agent_tick.add_argument("--pool-limit", type=int, default=20)
    agent_tick.add_argument("--json", action="store_true")
    agent_tick.add_argument("--force", action="store_true",
                            help="忽略已消费标记，执行最早已到条目")
    agent_sched = agent_sub.add_parser("schedule", help="AI 定时队列查看与设置")
    agent_sched_sub = agent_sched.add_subparsers(dest="agent_sched_command")
    agent_sched_list = agent_sched_sub.add_parser("list", help="查看当前定时队列与今日执行情况")
    agent_sched_list.add_argument("--json", action="store_true")
    agent_sched_set = agent_sched_sub.add_parser("set", help="设置定时队列（写本地文件，不进 git）")
    agent_sched_set.add_argument("--entry", action="append", default=None,
                                 help="队列条目，可重复：TIME=ACTION，如 --entry 09:00=sync --entry 16:45=trade；动作仅 sync/analyze/plan/trade")
    agent_sched_set.add_argument("--json", action="store_true")

    # nav
    nav_parser = subparsers.add_parser("nav", help="查看 NAV 历史")
    nav_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # history
    history_parser = subparsers.add_parser("history", help="查看历史记录")
    history_parser.add_argument("--type", choices=["orders", "fills"], default="orders")
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # cron-run
    cron_parser = subparsers.add_parser("cron-run", help="Cron 模式执行")
    cron_parser.add_argument("--symbols", nargs="*", default=None,
                             help="缺省=config.yaml 股票池")
    cron_parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    args = parser.parse_args()

    def emit(data, ok: bool = True, error: str | None = None):
        print(json.dumps({"ok": ok, "data": data, "error": error},
                         indent=2, ensure_ascii=False, default=str))

    def _resolve_syms(b, a):
        """解析股票池：CLI > pool-from > config；返回 (symbols, source_note, candidates)。"""
        from paper_trading.integration import resolve_pool

        pf = getattr(a, "pool_from", "config") or "config"
        tag = getattr(a, "pool_tag", "") or ""
        if not tag and pf == "screening":
            tag = (b.scheme.universe_tag or "")  # 方案自带母策略 tag
        return resolve_pool(getattr(a, "symbols", None), b.stock_pool, pf,
                            getattr(a, "pool_limit", 20) or 20, None, tag)

    if not args.command:
        parser.print_help()
        sys.exit(2)

    from paper_trading.utils.run_lock import run_lock

    try:
        bridge = TradingBridge(data_db=args.data_db, account_db=args.account_db,
                               config_path=args.config, secrets_path=args.secrets,
                               scheme=args.scheme)
    except Exception as e:
        emit(None, ok=False, error=f"Init failed: {e}")
        sys.exit(1)

    try:
        if args.command == "status":
            emit(bridge.get_status())

        elif args.command == "sync":
            syms, note, _ = _resolve_syms(bridge, args)
            with run_lock(args.lock_file):
                res = bridge.sync_data(syms)
            res["pool_source"] = note
            bridge.broker.log_operation(
                "sync", {"symbols": syms, "pool_source": note}, res.get("ok", False),
                {"updated": res.get("updated")}, None, None)
            emit(res, ok=res.get("ok", False), error=res.get("error"))
            if not res.get("ok"):
                sys.exit(3)

        elif args.command in ("run", "cron-run"):
            syms, note, _ = _resolve_syms(bridge, args)
            if getattr(args, "dry_run", False):
                # dry-run：信号+风控预览，不下单不记NAV
                all_bars = {}
                for sym in syms:
                    bars = bridge.data_db.get_bars(sym, limit=30)
                    if bars:
                        all_bars[sym] = bars
                latest = bridge.price_map(syms)
                signals = bridge.strategy.generate_signals(all_bars)
                positions = {p.symbol: p for p in bridge.broker.get_all_positions()}
                nav = bridge.broker.get_nav(latest)
                preview = []
                for sym, sig in signals.items():
                    ok, reason = bridge.risk.check_signal(
                        sig, latest.get(sym, 0.0), bridge.broker.get_cash(),
                        positions, nav.total_value, prices=latest)
                    preview.append({"symbol": sym, "direction": sig.direction.name,
                                    "volume": sig.volume, "price": sig.price,
                                    "pass": ok, "reason": reason})
                bridge.broker.log_operation(
                    "run:dry-run", {"symbols": syms, "pool_source": note}, True,
                    {"signals": len(preview)}, nav.cash, nav.total_value)
                emit({"signals": preview, "nav": nav.__dict__, "pool_source": note})
            else:
                with run_lock(args.lock_file):
                    res = bridge.run_daily(syms)
                res["pool_source"] = note
                nav = res.get("nav") or {}
                bridge.broker.log_operation(
                    "run", {"symbols": syms, "pool_source": note}, True,
                    {"signals": res.get("signals_generated"),
                     "orders": res.get("orders_executed")},
                    nav.get("cash"), nav.get("total_value"))
                emit(res)

        elif args.command == "buy":
            with run_lock(args.lock_file):
                r = bridge.place_order(args.symbol, "buy", args.volume, args.price)
                st = bridge.get_status()
                bridge.broker.log_operation(
                    "buy", {"symbol": args.symbol, "volume": args.volume,
                            "price": args.price},
                    r.get("ok", False),
                    {"status": r.get("status"), "error": r.get("error"),
                     "filled_price": r.get("price"), "commission": r.get("commission"),
                     "stamp_duty": r.get("stamp_duty"), "transfer_fee": r.get("transfer_fee")},
                    st["cash"], st["total_value"])
                emit(r, ok=r.get("ok", False), error=r.get("error"))
                if not r.get("ok"):
                    sys.exit(3)

        elif args.command == "sell":
            with run_lock(args.lock_file):
                r = bridge.place_order(args.symbol, "sell", args.volume, args.price)
                st = bridge.get_status()
                bridge.broker.log_operation(
                    "sell", {"symbol": args.symbol, "volume": args.volume,
                             "price": args.price},
                    r.get("ok", False),
                    {"status": r.get("status"), "error": r.get("error"),
                     "filled_price": r.get("price"), "commission": r.get("commission"),
                     "stamp_duty": r.get("stamp_duty"), "transfer_fee": r.get("transfer_fee")},
                    st["cash"], st["total_value"])
                emit(r, ok=r.get("ok", False), error=r.get("error"))
                if not r.get("ok"):
                    sys.exit(3)

        elif args.command == "names":
            targets = set(args.symbols or []) or (
                set(bridge.stock_pool)
                | set(bridge.data_db.get_pool_symbols())
                | {p.symbol for p in bridge.broker.get_all_positions()}
            )
            fresh = {}
            if targets:
                try:
                    fresh = _get_fetcher().fetch_stock_names(sorted(targets))
                except Exception as e:
                    logger.warning(f"Name fetch failed (offline?): {e}")
                if fresh:
                    bridge.data_db.upsert_stock_names(fresh)
            cached = bridge.data_db.get_stock_names()
            merged = {s: cached.get(s) or fresh.get(s) or "" for s in sorted(targets)}
            emit({"names": merged, "source": "live" if fresh else "cache"})

        elif args.command == "preview":
            emit(bridge.preview_order(args.symbol, args.direction, args.volume, args.price))

        elif args.command == "scheme":
            from paper_trading.strategy import (all_schemes, mother_strategies,
                                                set_active)

            from pathlib import Path as _P
            if args.scheme_command == "use":
                ok, msg = set_active(_P(__file__).resolve().parents[1], args.name,
                                     getattr(args, "instruction", None))
                # 切换后重载 bridge 以便后继命令即时生效由调用方重建；此处仅回显
                emit({"ok": ok, "message": msg} if ok else None,
                     ok=ok, error=None if ok else msg)
                if not ok:
                    sys.exit(3)
            else:
                ss = all_schemes()
                emit({"active": bridge.scheme.name, "source": bridge.scheme_source,
                      "schemes": [{"name": s.name, "title": s.title, "desc": s.desc,
                                   "source": s.source,
                                   "universe": s.universe_source +
                                   (f":{s.universe_tag}" if s.universe_tag else ""),
                                   "allow_buy": s.allow_buy, "exits": s.exits_note}
                                  for s in ss.values()],
                      "mother": mother_strategies()})

        elif args.command == "llm":
            if args.llm_command == "config":
                if args.show:
                    emit(bridge.llm_status())
                else:
                    if args.api_key:
                        logger.warning("Key 经命令行传入会留在 shell 历史里，敏感环境请改用面板设置页")
                    r = bridge.llm_save(args.preset, args.base_url, args.model, args.api_key)
                    emit(r, ok=r.get("ok", False), error=r.get("error"))
                    if not r.get("ok"):
                        sys.exit(3)
            elif args.llm_command == "models":
                r = bridge.llm_models()
                emit(r, ok=r.get("ok", False), error=r.get("error"))
                if not r.get("ok"):
                    sys.exit(3)
            elif args.llm_command == "ask":
                r = bridge.llm_ask(args.prompt, args.system)
                emit(r, ok=r.get("ok", False), error=r.get("error"))
                if not r.get("ok"):
                    sys.exit(3)
            else:
                emit(bridge.llm_status())

        elif args.command == "agent":
            from paper_trading.agent import AgentTrader

            if args.agent_command == "schedule":
                from paper_trading.agent import schedule as _sched

                if getattr(args, "agent_sched_command", None) == "set":
                    if not getattr(args, "entry", None):
                        emit(None, ok=False,
                             error="须指定至少一个 --entry（示例：--entry 09:00=sync --entry 16:45=trade）")
                        sys.exit(2)
                    ok, msg = _sched.save_schedule(".", args.entry)
                    emit({"message": msg} if ok else None, ok=ok,
                         error=None if ok else msg)
                    if not ok:
                        sys.exit(3)
                else:
                    from paper_trading.agent.schedule import due_entries as _due

                    cur = _sched.load_schedule(".")
                    emit({"entries": cur["entries"], "source": cur["source"],
                          "now": _sched._now().strftime("%H:%M"),
                          "due": _due(cur["entries"])})
                return
            if args.agent_command == "tick":
                syms, note, cands = _resolve_syms(bridge, args)
                with run_lock(args.lock_file):
                    trader = AgentTrader(bridge, bridge.agent_cfg)
                    res = trader.tick(syms, candidates=cands, pool_source=note,
                                      force=args.force)
                emit(res, ok=res.get("ok", False), error=res.get("error"))
                if not res.get("ok"):
                    sys.exit(3)
                return
            if args.agent_command != "run":
                parser.print_help()
                sys.exit(2)
            syms, note, cands = _resolve_syms(bridge, args)
            if not bridge.agent_cfg.enabled and not args.force:
                emit(None, ok=False, error="agent 未启用（config.yaml agent.enabled）")
                sys.exit(3)
            with run_lock(args.lock_file):
                trader = AgentTrader(bridge, bridge.agent_cfg)
                res = trader.run(syms, dry_run=args.dry_run, force=args.force,
                                 plan_only=args.plan_only, candidates=cands,
                                 pool_source=note,
                                 ignore_schedule=args.ignore_schedule)
            emit(res, ok=res.get("ok", False), error=res.get("error"))
            if not res.get("ok"):
                sys.exit(3)

        elif args.command == "nav":
            emit(bridge.get_nav_history())

        elif args.command == "history":
            if args.type == "orders":
                emit(bridge.get_order_history(args.limit))
            else:
                emit(bridge.get_fill_history(args.limit))
    except RuntimeError as e:
        emit(None, ok=False, error=str(e))
        sys.exit(4)
    except Exception as e:
        emit(None, ok=False, error=f"{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
