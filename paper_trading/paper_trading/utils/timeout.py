"""第三方抓取的硬超时兜底（无第三方依赖）。

akshare 底层是 requests，很多接口不暴露 timeout 参数；网络 stall 时
调用会无限期挂住。cron 每分钟一轮，一旦挂住还会长期持有 `run_lock`，
把引擎/止损一起拖死（Issue #1 的新闻源就是同一类问题）。

做法：把调用放进守护线程，主线程 `join(timeout)`；超时抛 `TimeoutError`。
被放弃的线程仍是 daemon（进程退出即回收），调用方按失败处理即可。
"""
from __future__ import annotations

import threading
from typing import Any, Callable


def call_with_timeout(fn: Callable[[], Any], timeout_sec: float) -> Any:
    """执行 `fn()`，超过 `timeout_sec` 秒抛 TimeoutError（异常原样透传）。"""
    box: dict = {}

    def _run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:      # noqa: BLE001 - 线程内异常带回主线程
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(float(timeout_sec))
    if t.is_alive():
        raise TimeoutError(f"call exceeded {float(timeout_sec):g}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")
