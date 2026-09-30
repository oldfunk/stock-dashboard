"""运行锁：防 cron + agent 并发重复下单（无第三方依赖）。"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _is_stale(p: Path) -> bool:
    """锁文件是否僵尸：PID 不存在/非法 → True（可删重抢）；
    活着或判不准 → False（保守等待，fail-closed 不双跑）。"""
    try:
        pid = int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return False


@contextmanager
def run_lock(lock_path: str | Path = "paper_trading.lock", timeout: float = 30.0) -> Iterator[None]:
    """独占锁文件；超时未获取则抛 RuntimeError（调用方转非0退出）。

    HOTFIX（母方 2026-09-30，协议 §5：生产自动交易被僵尸锁静默掐停）：
    占位进程被 SIGKILL/断连杀死时 finally 跑不到，锁文件永久残留，此后
    所有 tick/fire/run 每次白等 timeout 后失败。修复：占位时先判僵尸——
    文件内 PID 已不存在（或内容非法）则视为僵尸，直接删了重抢；
    活进程（或判不准如无权限）一律保守等待，绝不双跑。
    """
    p = Path(lock_path)
    start = time.time()
    while True:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            if _is_stale(p):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
                continue
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
