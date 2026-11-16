"""进程级 LLM 调用限速器（RPM 上限）。

为什么装在 ``AiAnalyzer.analyze_stock`` 入口：那是**所有**调用路径的唯一
收口——每日自动批次、面板手动队列、单股重试都走它，所以「批次 + 手动」合计
也不会超过上游 RPM（2026-11-16 用户告知 NVIDIA 上游 RPM=40）。

令牌间隔法：相邻两次调用开始时刻至少相隔 ``60/rpm`` 秒。比令牌桶简单，
且对"突发"天然平滑；多线程下用锁串行化"取号"，等待在锁外。
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_limiter: "RateLimiter | None" = None
_limiter_rpm = 0


class RateLimiter:
    def __init__(self, rpm: int):
        self.min_interval = (60.0 / rpm) if rpm and rpm > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.min_interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)


def get_limiter(rpm: int) -> RateLimiter:
    """取（必要时重建）进程级限速器；``rpm<=0`` 表示不限速。"""
    global _limiter, _limiter_rpm
    rpm = int(rpm or 0)
    with _lock:
        if _limiter is None or _limiter_rpm != rpm:
            _limiter = RateLimiter(rpm)
            _limiter_rpm = rpm
        return _limiter


def reset_limiter() -> None:
    """测试用：清空进程级限速器。"""
    global _limiter, _limiter_rpm
    with _lock:
        _limiter = None
        _limiter_rpm = 0
