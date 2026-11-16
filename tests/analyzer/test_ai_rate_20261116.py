"""进程级 RPM 限速器（2026-11-16，NVIDIA 上游 RPM=40）。"""

import time

from src import ai_rate


def test_limiter_enforces_spacing():
    ai_rate.reset_limiter()
    lim = ai_rate.get_limiter(60)          # 60 RPM -> 至少 1s 间隔
    t0 = time.monotonic()
    for _ in range(3):
        lim.wait()
    elapsed = time.monotonic() - t0
    assert elapsed >= 1.9, f"3 次调用应至少隔 2s，实际 {elapsed:.2f}s"


def test_rpm_zero_means_no_wait():
    ai_rate.reset_limiter()
    lim = ai_rate.get_limiter(0)
    t0 = time.monotonic()
    lim.wait()
    assert time.monotonic() - t0 < 0.05


def test_limiter_rebuilt_when_rpm_changes():
    ai_rate.reset_limiter()
    a = ai_rate.get_limiter(60)
    b = ai_rate.get_limiter(30)
    assert a is not b, "RPM 变了要重建（否则还是旧间隔）"
    assert ai_rate.get_limiter(30) is b, "同 RPM 复用同一实例"


def test_analyzer_reads_rpm_limit():
    from src.analyzer.ai_analyzer import AiAnalyzer
    ai_rate.reset_limiter()
    a = AiAnalyzer({"rpm_limit": 40, "api_key": "k", "api_base": "http://x"})
    assert a._rpm_limit == 40
    assert ai_rate.get_limiter(40).min_interval == 1.5
