"""配置加载：config.yaml 单一真相源。"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        raise RuntimeError("Need pyyaml: pip install pyyaml (for config.yaml)")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """加载配置并填充默认值。"""
    from paper_trading.models import TradingConfig

    raw = _read_yaml(path)
    trading = raw.get("trading", {})
    account = raw.get("account", {})
    strategy = raw.get("strategy", {})
    risk = raw.get("risk", {})
    agent = raw.get("agent", {})

    tcfg = TradingConfig(
        commission_rate=float(trading.get("commission_rate", 0.00025)),
        commission_min=float(trading.get("commission_min", 5.0)),
        stamp_duty_rate=float(trading.get("stamp_duty_rate", 0.0005)),
        transfer_fee_rate=float(trading.get("transfer_fee_rate", 0.00001)),
        slippage_fixed=float(trading.get("slippage_fixed", 0.01)),
        slippage_pct=float(trading.get("slippage_pct", 0.001)),
        use_slippage_pct=bool(trading.get("use_slippage_pct", False)),
        initial_cash=float(account.get("initial_cash", 1_000_000.0)),
        holidays=tuple(trading.get("holidays", []) or ()),
    )
    def _norm_sym(s) -> str:
        # YAML 1.1 会把 000333 这类全小数字解析成八进制 int（219）且不可逆，
        # 因此 config 里必须加引号；这里再兜底：纯数字补齐 6 位
        t = str(s).strip()
        return t.zfill(6) if t.isdigit() else t

    return {
        "raw": raw,
        "trading_config": tcfg,
        "strategy": {
            "active": str(strategy.get("active", "general") or "general"),
            "short_window": int(strategy.get("short_window", 5)),
            "long_window": int(strategy.get("long_window", 20)),
            "buy_volume": int(strategy.get("buy_volume", 100)),
            "sell_volume": int(strategy.get("sell_volume", 100)),
        },
        "risk": {
            "max_single_order_value": float(risk.get("max_single_order_value", 200_000.0)),
            "max_position_pct": float(risk.get("max_position_pct", 0.3)),
            "max_total_position_pct": float(risk.get("max_total_position_pct", 0.95)),
            "max_drawdown_pct": float(risk.get("max_drawdown_pct", 0.20)),
        },
        "stock_pool": [_norm_sym(s) for s in raw.get("stock_pool", [])],
        "agent": {
            "enabled": bool(agent.get("enabled", True)),
            "max_orders_per_run": int(agent.get("max_orders_per_run", 3)),
            "max_order_value": float(agent.get("max_order_value", 20000.0)),
            "daily_loss_halt_pct": float(agent.get("daily_loss_halt_pct", 0.05)),
            "llm_timeout": float(agent.get("llm_timeout", 120.0)),
            "llm_retries": int(agent.get("llm_retries", 1)),
            "llm_max_tokens": int(agent.get("llm_max_tokens", 8192)),
            "llm_thinking": str(agent.get("llm_thinking", "disabled")),
        },
    }
