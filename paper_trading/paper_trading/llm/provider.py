"""OpenAI 兼容接口调用（标准库 urllib，无第三方依赖）。

错误信息里永不携带 Key。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


class LLMError(Exception):
    """LLM 调用失败（网络/认证/协议）。message 中不含 Key。"""


@dataclass
class LLMConfig:
    base_url: str
    api_key: str = ""
    model: str = ""
    timeout: float = 30.0
    max_tokens: int = 4096  # 推理模型 thinking 占用输出预算，默认留足
    temperature: float = 0.2
    extra_body: dict = None  # 厂商透传参数（如 {"thinking": {"type": "disabled"}}）

    def __post_init__(self) -> None:
        if self.extra_body is None:
            self.extra_body = {}


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _request(cfg: LLMConfig, path: str, payload: Optional[dict] = None) -> dict:
    url = _join(cfg.base_url, path)
    if not url.startswith(("http://", "https://")):
        raise LLMError(f"base_url 非法（须 http(s) 开头）：{cfg.base_url!r}")
    data = json.dumps(payload or {}).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            body = ""
        raise LLMError(f"HTTP {e.code} {path}：{body}") from None
    except Exception as e:
        raise LLMError(f"请求失败 {path}：{type(e).__name__}: {e}") from None


def list_models(cfg: LLMConfig) -> list[str]:
    """拉取模型列表（GET {base}/models）。返回模型 id 列表。"""
    body = _request(cfg, "/models")
    items = body.get("data", [])
    if not isinstance(items, list):
        raise LLMError("模型列表格式异常（缺 data 数组）")
    out = [str(x.get("id", "")) for x in items if isinstance(x, dict) and x.get("id")]
    if not out:
        raise LLMError("模型列表为空（检查 Key 权限或 base_url）")
    return out


def chat(cfg: LLMConfig, messages: list[dict], system: str = "") -> str:
    """一轮对话，返回助手文本。"""
    if not cfg.model:
        raise LLMError("未指定模型（先选模型或手填模型名）")
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    payload = {
        "model": cfg.model,
        "messages": msgs,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if cfg.extra_body:
        payload.update(cfg.extra_body)
    body = _request(cfg, "/chat/completions", payload)
    try:
        return str(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        raise LLMError("对话返回格式异常（缺 choices/message/content）") from None
