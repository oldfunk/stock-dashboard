"""LLM 接入层（P1：只咨询，不交易）。

设计：
- 只认 OpenAI 兼容接口（市面上主流厂商全支持），手动输模型名永远可作兜底
- Key 永不进 git / 日志 / 面板明文回显，只落 0600 的本地 secrets 文件
- 下单链路零改动：LLM 只动嘴，风控与撮合仍是硬边界（P2 才接决策环）
"""
from .presets import PRESETS, preset_base_url
from .provider import LLMConfig, LLMError, chat, list_models
from .secrets import load_secrets, masked_key, public_provider_view, save_provider, save_secrets

__all__ = [
    "PRESETS", "preset_base_url",
    "LLMConfig", "LLMError", "chat", "list_models",
    "load_secrets", "save_provider", "save_secrets", "masked_key",
    "public_provider_view",
]
