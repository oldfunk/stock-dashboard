"""运行锁：防 cron + agent 并发重复下单（无第三方依赖）。

锁文件内容 = `pid 启动标识`。等待时不是死等到超时，而是检查持有者是否
仍然存活：进程被 SIGKILL/断电后锁文件会残留，旧实现会让后续 cron 永久
失败（每分钟都拿不到锁），只能人工删文件。

注意：探测存活**绝不能**在 Windows 上用 `os.kill(pid, 0)`——Windows 的
`os.kill` 对非控制台信号实际调用 `TerminateProcess`，`sig=0` 会直接杀进程。
这里 Windows 走 `OpenProcess` + `GetProcessTimes`，POSIX 走信号 0。
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Tuple


def _start_token(pid: int) -> str:
    """进程启动标识，用于区分 PID 复用（取不到就返回空串）。"""
    if pid <= 0:
        return ""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
            if not handle:
                return ""
            try:
                created = wintypes.FILETIME()
                exited = wintypes.FILETIME()
                kernel = wintypes.FILETIME()
                user = wintypes.FILETIME()
                ok = kernel32.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel), ctypes.byref(user))
                if not ok:
                    return ""
                return f"{created.dwHighDateTime:08x}{created.dwLowDateTime:08x}"
            finally:
                kernel32.CloseHandle(handle)
        except Exception:                       # noqa: BLE001 - 探测失败不阻断
            return ""
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
            data = f.read()
        # comm 字段可能含空格/括号：从最后一个 ')' 后切；starttime 是第 22 字段
        rest = data.rsplit(")", 1)[-1].split()
        return rest[19]
    except (OSError, IndexError):
        return ""


def _pid_alive(pid: int) -> bool:
    """进程是否存活（只查询，不发送任何信号）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True                 # 查不到就保守当存活，绝不误删
                return code.value == 259        # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:                       # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _read_lock(p: Path) -> Tuple[str, int, str]:
    """返回 (原始内容, pid, 启动标识)；读不到返回 ("", 0, "")。"""
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return "", 0, ""
    parts = raw.split()
    pid = 0
    token = ""
    if parts:
        try:
            pid = int(parts[0])
        except ValueError:
            pid = 0
    if len(parts) > 1:
        token = parts[1]
    return raw, pid, token


def _is_stale(raw: str, pid: int, token: str, p: Path,
              stale_after: float) -> bool:
    """持有者已死 / PID 被复用 → 可接管；无法判断时保守视为存活。"""
    if not pid:
        # 老格式/损坏内容：没法判活，只能按 mtime 兜底（且阈值较长）
        try:
            age = time.time() - p.stat().st_mtime
        except OSError:
            return False
        return age >= stale_after
    if not _pid_alive(pid):
        return True
    if token:
        cur = _start_token(pid)
        if cur and cur != token:
            return True                         # PID 被复用，原持有者已不存在
    return False


@contextmanager
def run_lock(lock_path: str | Path = "paper_trading.lock",
             timeout: float = 30.0,
             stale_after: float = 1800.0) -> Iterator[None]:
    """独占锁文件；持有者已死则自动接管，超时仍未获取抛 RuntimeError。

    `stale_after` 只用于“锁内容无法解析”的兜底，正常锁靠 PID/启动标识判定，
    不会误删仍在运行的守护进程（`engine --interval` 会全天持锁）。
    """
    p = Path(lock_path)
    me = f"{os.getpid()} {_start_token(os.getpid())}".strip()
    start = time.time()
    while True:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, me.encode())
            finally:
                os.close(fd)
            break
        except FileExistsError:
            raw, pid, token = _read_lock(p)
            if _is_stale(raw, pid, token, p, stale_after):
                # 内容比对后再删：避免与刚抢到锁的新进程竞态
                try:
                    if p.read_text(encoding="utf-8", errors="ignore").strip() == raw:
                        p.unlink()
                        continue
                except OSError:
                    pass
            if time.time() - start >= timeout:
                raise RuntimeError(
                    f"Another run is holding {p} (pid={pid or '?'}), abort")
            time.sleep(0.2)
    try:
        yield
    finally:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
