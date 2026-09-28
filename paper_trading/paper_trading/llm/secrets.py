"""本地密钥管理。

- 路径：环境变量 PAPER_SECRETS 优先，否则仓库根 `secrets.local.json`
  （已 gitignore，永远不提交）
- 文件权限 0600（Windows 下尽力而为）；读失败返回空
- 面板/API 只允许回显掩码，永不回显原文
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import stat
from pathlib import Path
from typing import Any, Optional

FILENAME = "secrets.local.json"


def secrets_path(explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("PAPER_SECRETS")
    if env:
        return Path(env)
    # paper_trading/llm/secrets.py -> 上两级即仓库根
    root = Path(__file__).resolve().parents[2]
    return root / FILENAME


def load_secrets(path: Optional[str] = None) -> dict[str, Any]:
    p = secrets_path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_secrets(data: dict[str, Any], path: Optional[str] = None) -> Path:
    p = secrets_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except Exception:
        pass
    return p


def save_provider(provider: dict[str, Any], path: Optional[str] = None) -> Path:
    """保存厂商配置（含 Key 原文，只落本地文件）。"""
    data = load_secrets(path)
    data["provider"] = {
        "preset": str(provider.get("preset", "custom")),
        "base_url": str(provider.get("base_url", "")),
        "model": str(provider.get("model", "")),
        "api_key": str(provider.get("api_key", "")),
    }
    if not data.get("admin_token"):
        data["admin_token"] = _secrets.token_urlsafe(32)
    return save_secrets(data, path)


def masked_key(key: str) -> str:
    """掩码回显：只露末 4 位，如 ****1234；空返回空串。"""
    if not key:
        return ""
    tail = key[-4:] if len(key) >= 4 else "****"
    return f"****{tail}"


def public_provider_view(path: Optional[str] = None) -> dict[str, Any]:
    """给面板/CLI 回显的安全视图（无 Key 原文、无 admin_token）。"""
    data = load_secrets(path)
    p = data.get("provider") or {}
    return {
        "configured": bool(p.get("base_url")),
        "preset": p.get("preset", ""),
        "base_url": p.get("base_url", ""),
        "model": p.get("model", ""),
        "key_masked": masked_key(p.get("api_key", "")),
        "has_key": bool(p.get("api_key")),
    }
