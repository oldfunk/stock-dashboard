"""基准指数日线刷新（A5b，2026-11-23）。

16:00 流水线后的首刷常遇到：东财 `RemoteDisconnected` + 新浪指数收盘尚未发布
（实测 11-23 16:04 首刷 0 行，20:14 重刷拿到 11-23）。20:45 再刷一次；
`refresh_benchmarks` 是幂等 upsert，重复跑不会产生重复行。

用法：`python scripts/refresh_benchmarks.py`（生产 cron 20:45）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.outcome_service import refresh_benchmarks  # noqa: E402


def main() -> int:
    stats = refresh_benchmarks(load_config())
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
