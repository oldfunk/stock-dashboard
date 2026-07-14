"""股票看板 - 统一日志配置

所有入口（src.main / src.orchestrator / scripts/*）共享同一套日志：
- 控制台：INFO 级别，便于前台查看
- 文件：data/logs/dashboard.log，按 5MB 轮转、保留 7 份，避免单文件无限膨胀

调用方式：
    from src.logging_config import setup_logging
    setup_logging()
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(level: int = logging.INFO) -> logging.Handler:
    """配置根日志：输出到控制台 + 轮转文件。

    多次调用安全：会清理旧 handler 避免重复输出。
    """
    root = logging.getLogger()
    root.setLevel(level)

    # 避免重复挂载
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(level)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    # 轮转文件
    log_dir = Path(__file__).parent.parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "dashboard.log"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=7, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    return file_handler
