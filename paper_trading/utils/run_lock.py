"""运行锁：防 cron + agent 并发重复下单（无第三方依赖）。"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def run_lock(lock_path: str | Path = "paper_trading.lock", timeout: float = 30.0) -> Iterator[None]:
    """独占锁文件；超时未获取则抛 RuntimeError（调用方转非0退出）。"""
    p = Path(lock_path)
    start = time.time()
    while True:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - start >= timeout:
                raise RuntimeError(f"Another run is holding {p}, abort")
            time.sleep(0.2)
    try:
        yield
    finally:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
