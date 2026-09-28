"""主流厂商预设（均为 OpenAI 兼容接口地址，用户可改 base_url 指向网关/代理）。"""
from __future__ import annotations

PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "label": "通义千问（阿里）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "moonshot": {
        "label": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
    },
    "glm": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
    },
    "doubao": {
        "label": "豆包（火山引擎 Ark）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "custom": {
        "label": "自定义（兼容网关/代理）",
        "base_url": "",
        "default_model": "",
    },
}


def preset_base_url(preset: str) -> str:
    """取预设地址，未知预设返回空串（调用方要求用户手填）。"""
    return PRESETS.get(preset, {}).get("base_url", "")
