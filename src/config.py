"""
股票看板 - 配置加载

统一入口：load_config() 读取 config/config.yaml，并叠加 config/local.yaml 覆盖。
所有模块（orchestrator / web / scripts）都应通过本模块加载配置，
避免 local.yaml 支持不一致的问题。
"""

from pathlib import Path
import yaml

_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"
_LOCAL_PATH = _PROJECT_ROOT / "config" / "local.yaml"


def _deep_merge(base: dict, override: dict) -> None:
    """将 override 深合并到 base（原地修改）"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_config() -> dict:
    """加载主配置 + local.yaml 覆盖。失败时返回空 dict 而非抛异常。"""
    if not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if _LOCAL_PATH.exists():
        with open(_LOCAL_PATH, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        _deep_merge(config, local)
    return config


def project_root() -> Path:
    """返回项目根目录 Path"""
    return _PROJECT_ROOT
