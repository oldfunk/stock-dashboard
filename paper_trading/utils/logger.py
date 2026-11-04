"""日志工具。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

#: `--json` 模式：新 logger 直接写 stderr，stdout 保持纯 JSON
_TO_STDERR = False


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """获取配置好的 logger。"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.__stderr__ if _TO_STDERR else sys.stdout)
        fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger


def use_stderr() -> None:
    """把日志切到 stderr（`--json` 模式保证 stdout 纯 JSON）。

    用 `sys.__stderr__`（真实 fd）而不是 `sys.stderr`：后者会被测试框架/
    shell 重定向替换，复用旧对象会在退出时关闭报错。
    """
    global _TO_STDERR
    _TO_STDERR = True
    loggers = [logging.getLogger()]
    loggers += [l for l in logging.Logger.manager.loggerDict.values()
                if isinstance(l, logging.Logger)]
    for lg in loggers:
        for h in lg.handlers:
            # 只动我们自己建的普通 StreamHandler，不碰测试框架的捕获 handler
            if type(h) is logging.StreamHandler:
                h.stream = sys.__stderr__
