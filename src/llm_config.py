"""LLM 自带 Key 接入配置（OpenAI-compatible v1）。

职责：厂商预设、模型列表拉取、配置持久化、脱敏状态查询。
- Key 只存 `.env`（STOCK_AI_*，gitignored），非敏感项存 `config/local.yaml` ai 段。
- Key 绝不记日志、绝不原样返回前端；api_base 统一去掉末尾 `/chat/completions` 后缀。
- 文件路径均可注入（单测用 tmp_path），默认走项目根。
"""

import logging
import os
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger(__name__)

PROVIDERS = [
    {"id": "openai", "name": "OpenAI", "api_base": "https://api.openai.com/v1"},
    {"id": "deepseek", "name": "DeepSeek", "api_base": "https://api.deepseek.com/v1"},
    {"id": "moonshot", "name": "Moonshot（Kimi）", "api_base": "https://api.moonshot.cn/v1"},
    {"id": "zhipu", "name": "智谱 GLM", "api_base": "https://open.bigmodel.cn/api/paas/v4"},
    {"id": "qwen", "name": "通义千问", "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"id": "doubao", "name": "火山引擎（豆包）", "api_base": "https://ark.cn-beijing.volces.com/api/v3"},
    {"id": "openrouter", "name": "OpenRouter", "api_base": "https://openrouter.ai/api/v1"},
    {"id": "siliconflow", "name": "硅基流动", "api_base": "https://api.siliconflow.cn/v1"},
    {"id": "ollama", "name": "Ollama（本地）", "api_base": "http://localhost:11434/v1"},
    {"id": "custom", "name": "自定义", "api_base": ""},
]

ENV_KEY = "STOCK_AI_API_KEY"
ENV_BASE = "STOCK_AI_API_BASE"
ENV_MODEL = "STOCK_AI_MODEL"


class LLMSetupError(Exception):
    """连接测试/保存失败（对外展示用，消息里绝不含 Key）"""


def _default_env_path() -> Path:
    from src.config import project_root
    return project_root() / ".env"


def _default_local_path() -> Path:
    from src.config import project_root
    return project_root() / "config" / "local.yaml"


def normalize_base(api_base: str) -> str:
    """归一化 api_base：去空白/尾斜杠，去 /chat/completions 后缀，校验 http(s)。"""
    base = (api_base or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    if not base.startswith(("http://", "https://")):
        raise LLMSetupError("api_base 必须以 http:// 或 https:// 开头")
    return base


def fetch_models(api_base: str, api_key: str = "", timeout: float = 15.0) -> list:
    """GET {base}/models 拉模型 id 列表；失败抛 LLMSetupError（不含 Key）。"""
    base = normalize_base(api_base)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base}/models", headers=headers)
    except Exception as e:
        raise LLMSetupError(f"连接失败：{type(e).__name__}") from e
    if resp.status_code == 401:
        raise LLMSetupError("鉴权失败（401），请检查 API Key")
    if resp.status_code != 200:
        raise LLMSetupError(f"模型列表 HTTP {resp.status_code}")
    try:
        ids = [m["id"] for m in (resp.json().get("data") or []) if m.get("id")]
    except Exception as e:
        raise LLMSetupError("模型列表解析失败") from e
    if not ids:
        raise LLMSetupError("模型列表为空（请改用手动输入模型名）")
    return ids


def mask_key(value: str) -> str:
    """脱敏：长 key 留前 4 后 4，其余 ****；空返回空串。"""
    if not value:
        return ""
    v = str(value)
    return v[:4] + "****" + v[-4:] if len(v) > 8 else "****"


def _read_local_ai(local_path: Path) -> dict:
    try:
        with open(local_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    ai = cfg.get("ai")
    return ai if isinstance(ai, dict) else {}


def _effective() -> dict:
    """生效值：env 优先（与 AiAnalyzer 一致），其次 local.yaml ai 段。"""
    local_ai = _read_local_ai(_default_local_path())
    return {
        "api_base": os.getenv(ENV_BASE) or local_ai.get("api_base") or "",
        "model": os.getenv(ENV_MODEL) or local_ai.get("model") or "",
        "provider": local_ai.get("provider") or "",
    }


def llm_status() -> dict:
    """脱敏状态：has_key 只有布尔值，key_preview 脱敏，绝不返回原值。"""
    key = os.getenv(ENV_KEY) or ""
    eff = _effective()
    return {
        "configured": bool(key),
        "has_key": bool(key),
        "key_preview": mask_key(key),
        "api_base": eff["api_base"],
        "model": eff["model"],
        "provider": eff["provider"],
    }


def save_llm_config(provider: str, api_base: str, model: str,
                    api_key: str = "", temperature: float = 0.3,
                    max_tokens: int = 6000,
                    env_path: Path = None, local_path: Path = None) -> dict:
    """保存 LLM 配置：key → .env，非敏感 → local.yaml ai 段；同时写 os.environ 即时生效。

    api_key 为空表示保留现有 Key（不覆盖）。返回脱敏状态。
    """
    provider = (provider or "custom").strip()
    if provider not in {p["id"] for p in PROVIDERS}:
        raise LLMSetupError(f"未知 provider：{provider}")
    base = normalize_base(api_base)
    model = (model or "").strip()
    if not model:
        raise LLMSetupError("模型名不能为空（可从列表选择或手动输入）")
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        raise LLMSetupError("temperature 必须是数字") from None
    if not 0 <= temperature <= 2:
        raise LLMSetupError("temperature 须在 0~2 之间")
    try:
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        raise LLMSetupError("max_tokens 必须是整数") from None
    if max_tokens <= 0:
        raise LLMSetupError("max_tokens 须为正整数")

    env_path = Path(env_path) if env_path else _default_env_path()
    local_path = Path(local_path) if local_path else _default_local_path()

    # ── .env：保留其他行，只 upsert 三个 STOCK_AI_* ──
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    except OSError:
        lines = []
    want = {ENV_BASE: base, ENV_MODEL: model}
    if api_key:
        want[ENV_KEY] = api_key
    seen = set()
    out = []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if name in want:
            out.append(f"{name}={want[name]}")
            seen.add(name)
        else:
            out.append(line)
    for name, val in want.items():
        if name not in seen:
            out.append(f"{name}={val}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    # ── local.yaml：只动 ai 段，其他键原样保留 ──
    try:
        with open(local_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    ai = cfg.get("ai")
    if not isinstance(ai, dict):
        ai = {}
    ai.update({"provider": provider, "api_base": base, "model": model,
               "temperature": temperature, "max_tokens": max_tokens})
    cfg["ai"] = ai
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

    # ── 即时生效（不等重启；重启后由 .env 恢复）──
    os.environ[ENV_BASE] = base
    os.environ[ENV_MODEL] = model
    if api_key:
        os.environ[ENV_KEY] = api_key
    logger.info("[LLM配置] 已保存 provider=%s model=%s", provider, model)
    return llm_status()
