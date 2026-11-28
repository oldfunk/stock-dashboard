"""纸盘只读桥（E0，2026-11-21）。

母项目**只读**子项目的 `paper_trading/data.db` 与 `paper_account.db`，
把账户/持仓/计划/成交/NAV 变成可消费的结构；分钟线与统一绩效在 E1/E2。

纪律（见 docs/paper-bridge.md）：
- 只读：SQLite URI `mode=ro` + `PRAGMA query_only=1`；
- 显式实例：默认只认合并树 `paper_trading/`，**不自动发现 :8080**；
- 失败降级：库缺失/被锁/缺列 → 返回 `{"source": "unavailable", "reason": ...}`，
  调用方按"无纸盘上下文"继续，绝不阻断主流程。
"""
from .panel import load_panel_snapshot
from .settings import bridge_paths
from .state import (
    load_account,
    load_latest_pending_plan,
    load_nav,
    load_pending_plan,
    load_positions,
    load_recent_fills,
    load_status,
)

__all__ = [
    "bridge_paths",
    "load_account",
    "load_positions",
    "load_pending_plan",
    "load_latest_pending_plan",
    "load_recent_fills",
    "load_nav",
    "load_status",
    "load_panel_snapshot",
]
