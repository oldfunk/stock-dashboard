"""面板 import 门禁：dashboard.py 必须可导入（语法错误直接发版会导致 Pi 服务崩溃循环）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_dashboard_imports():
    import paper_trading.dashboard as d

    assert hasattr(d, "Handler") and hasattr(d, "main")
    assert "market-row" in d.PAGE and "/api/quotes" in d.PAGE
    assert "AI 定时" in d.PAGE and "/api/agent/schedule" in d.PAGE


def test_entry_modules_import():
    """所有入口模块必须可导入（曾带着语法错误发版过）。"""
    import paper_trading.agent.loop
    import paper_trading.cli
    import paper_trading.main

    assert hasattr(paper_trading.cli, "main")
    assert hasattr(paper_trading.agent.loop, "AgentTrader")


def test_schemes_have_source():
    """Scheme 必带 source/allow_buy（cli scheme list 序列化依赖；缺失曾 500）。"""
    from paper_trading.strategy.schemes import all_schemes
    ss = all_schemes()
    assert set(ss) == {"mother", "general", "custom"} or set(ss) >= {"general", "custom"}
    for name, s in ss.items():
        assert s.source, name
        assert isinstance(s.allow_buy, bool), name


def test_takeover_token(tmp_path):
    """凭 Key 接管：对 Key 放行并返回可存口令；错 Key/空配置拒绝。"""
    from paper_trading.llm.secrets import save_provider
    from paper_trading.dashboard import _takeover_token
    p = save_provider({"preset": "custom", "base_url": "https://x.test/v1",
                       "model": "m", "api_key": "sk-fake-key"},
                      path=str(tmp_path / "s.json"))
    import json as _json
    stored = _json.loads(p.read_text(encoding="utf-8"))
    token = stored["admin_token"]
    assert token
    assert _takeover_token({"api_key": "sk-fake-key"}, str(tmp_path / "s.json")) == token
    assert _takeover_token({"api_key": "wrong"}, str(tmp_path / "s.json")) == ""
    assert _takeover_token({}, str(tmp_path / "s.json")) == ""
    assert _takeover_token({"api_key": "k"}, str(tmp_path / "nope.json")) == ""
