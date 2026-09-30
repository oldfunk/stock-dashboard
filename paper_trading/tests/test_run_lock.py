"""run_lock 僵尸锁单测（hermetic：tmp 路径 + 本进程 PID，无网络）。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.utils import run_lock as rl  # noqa: E402


def test_acquire_release(tmp_path):
    p = tmp_path / "a.lock"
    with rl.run_lock(p, timeout=5):
        assert p.exists()
    assert not p.exists()


def test_stale_dead_pid_is_reclaimed(tmp_path):
    p = tmp_path / "b.lock"
    p.write_text("999999999", encoding="utf-8")  # 不存在的 PID
    assert rl._is_stale(p) is True
    with rl.run_lock(p, timeout=5):
        assert p.exists()
    assert not p.exists()


def test_stale_garbage_is_reclaimed(tmp_path):
    p = tmp_path / "c.lock"
    p.write_text("not-a-pid", encoding="utf-8")
    assert rl._is_stale(p) is True
    with rl.run_lock(p, timeout=5):
        pass
    assert not p.exists()


def test_live_pid_is_not_stolen(tmp_path):
    p = tmp_path / "d.lock"
    p.write_text(str(os.getpid()), encoding="utf-8")  # 活着的进程
    assert rl._is_stale(p) is False
