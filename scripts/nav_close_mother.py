"""母侧纸盘收盘 NAV 兜底（A1b，2026-11-23）。

为什么需要：16:30 计划窗口要求"当日 raw 收盘已入库"才写 NAV；实测
Sina raw 在 15:50 还没发布（`day_no_today_bar=3`），所以 16:30 会跳过。
20:40 的 raw 拉取之后用本脚本补写；幂等（`has_nav_for_day` 已有行就返回）。

只写 `nav_history` + `op_log[nav:close]`，不下单、不改持仓。
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PT = ROOT / "paper_trading"
sys.path.insert(0, str(PT))
os.chdir(PT)

from paper_trading.agent.loop import AgentTrader  # noqa: E402
from paper_trading.cli import TradingBridge  # noqa: E402


def main() -> int:
    day = date.today().isoformat()
    b = TradingBridge()
    held = [p.symbol for p in b.broker.get_all_positions() if p.total_volume]
    if not held:
        print(f"{day} 无持仓，跳过 NAV")
        return 0
    t = AgentTrader(b, b.agent_cfg)
    t._snapshot_close_nav(held, day)
    print(f"{day} NAV close 完成（持仓 {len(held)} 只）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
