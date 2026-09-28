"""面板 import 门禁：dashboard.py 必须可导入（语法错误直接发版会导致 Pi 服务崩溃循环）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_dashboard_imports():
    import paper_trading.dashboard as d

    assert hasattr(d, "Handler") and hasattr(d, "main")
    assert "market-row" in d.PAGE and "/api/quotes" in d.PAGE


def test_entry_modules_import():
    """所有入口模块必须可导入（曾带着语法错误发版过）。"""
    import paper_trading.agent.loop
    import paper_trading.cli
    import paper_trading.main

    assert hasattr(paper_trading.cli, "main")
    assert hasattr(paper_trading.agent.loop, "AgentTrader")
