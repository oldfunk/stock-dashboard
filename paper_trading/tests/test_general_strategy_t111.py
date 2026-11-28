"""T111：默认通用中线策略（AI playbook + MA20/60 + 配置接线）。"""
from pathlib import Path
from types import SimpleNamespace

import yaml

from paper_trading.agent.loop import AgentTrader
from paper_trading.agent.prompts import SCHEME_TMPL
from paper_trading.strategy.schemes import all_schemes


def test_general_scheme_default_playbook():
    s = all_schemes()["general"]
    assert s.title == "通用中线趋势"
    assert s.signal["short_window"] == 20 and s.signal["long_window"] == 60
    assert "MA20" in s.rules and "MA60" in s.rules
    assert "−7%" in s.rules and "20 个交易日" in s.rules
    assert "健康度" in s.rules and "不自动拦买" in s.rules


def test_scheme_txt_injects_rules():
    s = all_schemes()["general"]
    t = AgentTrader.__new__(AgentTrader)
    t.bridge = SimpleNamespace(scheme=s)
    txt = t._scheme_txt()
    assert "方案规则" in txt and "MA20" in txt and "禁止补跌" in txt
    # 模板本身也可独立格式化（防新增占位符漏传）
    assert "方案规则" in SCHEME_TMPL.format(
        title=s.title, name=s.name, desc=s.desc, rules=s.rules, exits=s.exits_note)


def test_config_default_windows_aligned():
    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert raw["strategy"]["short_window"] == 20
    assert raw["strategy"]["long_window"] == 60
