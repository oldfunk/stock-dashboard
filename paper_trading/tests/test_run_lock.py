"""运行锁：互斥、正常释放、残留锁接管（断电/SIGKILL 后 cron 不永久失败）。"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.utils.run_lock import _start_token, run_lock  # noqa: E402


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_lock_creates_and_releases(tmp_path):
    p = tmp_path / "run.lock"
    with run_lock(p, timeout=1.0):
        assert p.exists()
        assert p.read_text().split()[0] == str(os.getpid())
    assert not p.exists()


def test_stale_lock_from_dead_process_is_taken_over(tmp_path):
    """进程已死 → 立即接管，而不是等满 timeout 后永久失败。"""
    p = tmp_path / "run.lock"
    p.write_text(f"{_dead_pid()} dead-token")
    t0 = time.time()
    with run_lock(p, timeout=1.0):
        assert time.time() - t0 < 1.0
    assert not p.exists()


def test_live_lock_is_not_stolen(tmp_path):
    """持有者仍存活（老格式只有 PID）时绝不删锁，只能超时失败。"""
    p = tmp_path / "run.lock"
    p.write_text(str(os.getpid()))
    with pytest.raises(RuntimeError):
        with run_lock(p, timeout=0.6):
            pass
    assert p.exists()                       # 不能被等待方误删
    p.unlink()


def test_pid_reuse_mismatched_token_is_stale(tmp_path):
    """PID 活着但启动标识对不上 = PID 被复用，原持有者已不存在。"""
    if not _start_token(os.getpid()):
        pytest.skip("当前平台取不到进程启动标识")
    p = tmp_path / "run.lock"
    p.write_text(f"{os.getpid()} definitely-not-this-process")
    with run_lock(p, timeout=1.0):
        pass
    assert not p.exists()
