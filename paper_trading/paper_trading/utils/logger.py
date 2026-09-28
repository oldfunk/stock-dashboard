"""日志工具。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """获取配置好的 logger。"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger
