"""配置加载：config.yaml 单一真相源。"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

#: `TradingConfig` 的字段名（config.yaml 的 trading 段按这些名字覆盖）
_TRADING_FIELDS = None


def _trading_fields() -> set[str]:
    global _TRADING_FIELDS
    if _TRADING_FIELDS is None:
        from paper_trading.models import TradingConfig

        _TRADING_FIELDS = {f.name for f in dataclasses.fields(TradingConfig)}
    return _TRADING_FIELDS


def trading_config_from_dicts(trading: dict, account: dict):
    """config.yaml 的 `trading:`/`account:` 两段盖到 `TradingConfig` 上（T51 唯一构造点）。

    **这里一个数字都不写**：兜底值只存在于 `TradingConfig` 的字段默认里。
    原先 `float(trading.get("commission_rate", 0.00025))` 这种写法在四处各抄一遍，
    每抄一次就多一条"改了 config 也不生效"的路（纪律 7 口径唯一）。
    """
    from paper_trading.models import TradingConfig

    cfg = TradingConfig()
    unknown: list[str] = []
    for key, val in (trading or {}).items():
        if key not in _trading_fields():
            unknown.append(str(key))
            continue
        if key == "holidays":
            cfg.holidays = tuple(val or ())
        elif key == "use_slippage_pct":
            cfg.use_slippage_pct = bool(val)
        else:
            setattr(cfg, key, float(val))
    if account and account.get("initial_cash") is not None:
        cfg.initial_cash = float(account["initial_cash"])
    if unknown:
        # 不静默吞：键名写错（如 commission-rate）与"没配"是两件事，后者才是默认值
        from paper_trading.utils import get_logger

        get_logger(__name__).warning(
            "config.yaml 的 trading 段有未知键，已忽略：%s", unknown)
    return cfg


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
    raw = _read_yaml(path)
    trading = raw.get("trading", {})
    account = raw.get("account", {})
    strategy = raw.get("strategy", {})
    risk = raw.get("risk", {})
    agent = raw.get("agent", {})

    tcfg = trading_config_from_dicts(trading, account)
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
            "daily_loss_halt_pct": float(risk.get("daily_loss_halt_pct",
                                                  agent.get("daily_loss_halt_pct", 0.05))),
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
            "ask_max_tokens": int(agent.get("ask_max_tokens", 1536)),
            "llm_thinking": str(agent.get("llm_thinking", "disabled")),
            "picks_enabled": bool(agent.get("picks_enabled", True)),
            "picks_max": int(agent.get("picks_max", 5)),
            "conditions_enabled": bool(agent.get("conditions_enabled", True)),
            "max_conditions": int(agent.get("max_conditions", 5)),
            "condition_max_days": int(agent.get("condition_max_days", 3)),
            "event_enabled": bool(agent.get("event_enabled", True)),
            "event_price_move_pct": float(agent.get("event_price_move_pct", 3.0)),
            "event_drawdown_pct": float(agent.get("event_drawdown_pct", 5.0)),
            "event_cooldown_min": int(agent.get("event_cooldown_min", 30)),
            "event_min_interval_min": int(agent.get("event_min_interval_min", 15)),
            "event_max_calls_per_day": int(agent.get("event_max_calls_per_day", 6)),
            "event_max_conditions": int(agent.get("event_max_conditions", 3)),
            "news_enabled": bool(agent.get("news_enabled", True)),
            "news_limit": int(agent.get("news_limit", 12)),
            "news_timeout_sec": float(agent.get("news_timeout_sec", 15.0)),
            # 安全门禁（v0.7.6：漏白名单 = config 写了也失效，见 wiring_audit）
            "require_stop": bool(agent.get("require_stop", False)),
            "require_basis": bool(agent.get("require_basis", False)),
            "max_price_deviation": float(agent.get("max_price_deviation", 0.30)),
            "context_budget": int(agent.get("context_budget", 8000)),
        },
    }
