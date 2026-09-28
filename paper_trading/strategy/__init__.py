"""策略模块。"""
from .base_strategy import BaseStrategy
from .ma_cross_strategy import MACrossStrategy
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

__all__ = ["BaseStrategy", "MACrossStrategy", "CUSTOM_ID", "GENERAL_ID",
           "MOTHER_ID", "active_name", "all_schemes", "builtin_schemes",
           "custom_instruction", "mother_strategies",
           "resolve_scheme", "set_active"]
