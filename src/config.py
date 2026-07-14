"""
股票看板 - 配置加载

统一入口：load_config() 读取 config/config.yaml，并叠加 config/local.yaml 覆盖。
所有模块（orchestrator / web / scripts）都应通过本模块加载配置，
避免 local.yaml 支持不一致的问题。

支持配置热重载：监听 config.yaml / local.yaml 变化，自动更新内存中的配置。
"""

from pathlib import Path
import yaml
import threading
import time

_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"
_LOCAL_PATH = _PROJECT_ROOT / "config" / "local.yaml"

# 全局配置缓存和锁
_config_cache: dict = {}
_config_lock = threading.RLock()
_config_mtime: float = 0
_watcher_thread: threading.Thread | None = None
_watcher_stop = threading.Event()


def _deep_merge(base: dict, override: dict) -> None:
    """将 override 深合并到 base（原地修改）"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _load_config_from_disk() -> dict:
    """从磁盘加载配置（内部使用，不加锁）"""
    if not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if _LOCAL_PATH.exists():
        with open(_LOCAL_PATH, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        _deep_merge(config, local)
    return config


def load_config() -> dict:
    """加载主配置 + local.yaml 覆盖。失败时返回空 dict 而非抛异常。

    线程安全：使用全局缓存，若配置文件有变化会自动热重载。
    """
    global _config_cache, _config_mtime

    with _config_lock:
        # 检查文件是否有变化
        current_mtime = 0
        if _CONFIG_PATH.exists():
            current_mtime = max(current_mtime, _CONFIG_PATH.stat().st_mtime)
        if _LOCAL_PATH.exists():
            current_mtime = max(current_mtime, _LOCAL_PATH.stat().st_mtime)

        if current_mtime > _config_mtime:
            _config_cache = _load_config_from_disk()
            _config_mtime = current_mtime
            print(f"[Config] 热重载配置: {_CONFIG_PATH}")

        return dict(_config_cache)  # 返回副本，防止外部修改


def start_config_watcher(interval: float = 2.0) -> None:
    """启动配置文件监听线程（后台守护线程）。

    Args:
        interval: 检查间隔（秒），默认 2 秒
    """
    global _watcher_thread

    def _watch():
        while not _watcher_stop.is_set():
            load_config()  # 内部会检查 mtime 并自动重载
            _watcher_stop.wait(interval)

    if _watcher_thread is None or not _watcher_thread.is_alive():
        _watcher_stop.clear()
        _watcher_thread = threading.Thread(target=_watch, daemon=True, name="ConfigWatcher")
        _watcher_thread.start()
        print(f"[Config] 配置监听器已启动，间隔 {interval}s")


def stop_config_watcher() -> None:
    """停止配置文件监听线程"""
    _watcher_stop.set()
    if _watcher_thread and _watcher_thread.is_alive():
        _watcher_thread.join(timeout=5)


def project_root() -> Path:
    """返回项目根目录 Path"""
    return _PROJECT_ROOT


def get_config_path() -> Path:
    """返回主配置文件路径"""
    return _CONFIG_PATH
