"""策略模块。"""
from .base_strategy import BaseStrategy
from .breakout_strategy import BreakoutStrategy
from .ma_cross_strategy import MACrossStrategy
from .registry import (
    CompositeStrategy,
    StrategyConfigError,
    build,
    build_all,
    build_from_config,
    default_specs,
    load_specs,
)
from .rsi_strategy import RSIStrategy
from .schemes import (
    CUSTOM_ID,
    GENERAL_ID,
    MOTHER_ID,
    active_name,
    all_schemes,
    builtin_schemes,
    custom_instruction,
    mother_strategies,
    resolve_scheme,
    set_active,
)

__all__ = ["BaseStrategy", "BreakoutStrategy", "MACrossStrategy", "RSIStrategy",
           "CompositeStrategy", "StrategyConfigError", "build", "build_all",
           "build_from_config", "default_specs", "load_specs",
           "CUSTOM_ID", "GENERAL_ID",
           "MOTHER_ID", "active_name", "all_schemes", "builtin_schemes",
           "custom_instruction", "mother_strategies",
           "resolve_scheme", "set_active"]
