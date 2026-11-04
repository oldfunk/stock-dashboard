"""R2.2 配置接线回归：配置链是双层白名单，两层任一漏字段 = 配置静默失效。

链路：config.yaml → utils.config.load_config（归一化白名单）→
cli.TradingBridge 的 AgentConfig(...) 构造（白名单）→ 运行时读取。
2026-10-27 事故后续发现 require_stop 等三字段两层全漏、news_*/context_budget
构造层漏——本测试锁死三件套，新增 AgentConfig 字段时必须同步补两处。
"""
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 有意不从 yaml 接线的字段（运行期/CLI 显式传入，不是配置项）
WIRING_EXEMPT = {"dry_run"}


@pytest.fixture(scope="module")
def wiring():
    from paper_trading.agent.loop import AgentConfig

    fields = set(AgentConfig.__dataclass_fields__) - WIRING_EXEMPT
    cli_src = (ROOT / "paper_trading" / "cli.py").read_text(encoding="utf-8")
    m = re.search(r"self\.agent_cfg = AgentConfig\((.*?)\n        \)", cli_src, re.S)
    assert m, "cli.py 找不到 AgentConfig 构造，正则需随代码结构调整"
    wired = set(re.findall(r"(\w+)=", m.group(1)))
    cfg_src = (ROOT / "paper_trading" / "utils" / "config.py").read_text(encoding="utf-8")
    am = re.search(r'"agent": \{\n(.*?)\n        \}', cfg_src, re.S)
    assert am, "utils/config.py 找不到 agent 归一化块，正则需随代码结构调整"
    norm = set(re.findall(r'"(\w+)":', am.group(1)))
    return fields, wired, norm


def test_all_agent_config_fields_wired_in_cli(wiring):
    """第一层：AgentConfig 每个字段必须在 cli.py 构造中显式读取。"""
    fields, wired, _ = wiring
    missing = fields - wired
    assert not missing, (
        f"AgentConfig 字段未接线（config 改了也无效）：{sorted(missing)}；"
        f"在 cli.py 的 AgentConfig(...) 补 agent_kwargs.get(...)，"
        f"或加入 WIRING_EXEMPT 并写明理由")


def test_all_agent_config_fields_normalized(wiring):
    """第二层：字段必须进 utils/config.py 的 load_config 归一化白名单。"""
    fields, _, norm = wiring
    missing = fields - norm
    assert not missing, (
        f"字段漏出 load_config 白名单（yaml 值在归一化层被丢弃）："
        f"{sorted(missing)}")


def test_wired_keys_exist_on_agentconfig(wiring):
    """反向：cli.py 不许传 AgentConfig 不存在的字段（防拼写错误静默失效）。"""
    from paper_trading.agent.loop import AgentConfig

    fields, wired, _ = wiring
    orphan = wired - fields
    assert not orphan, f"cli 传了 AgentConfig 没有的字段：{sorted(orphan)}"


def test_safety_gate_fields_present_and_documented():
    """事故门禁三件套必须存在且 config.yaml 有键（2026-10-27 事故教训）。"""
    from paper_trading.agent.loop import AgentConfig

    d = AgentConfig.__dataclass_fields__
    assert d["require_stop"].default is False
    assert d["require_basis"].default is False
    assert d["max_price_deviation"].default == 0.30
    yaml_src = (ROOT / "config.yaml").read_text(encoding="utf-8")
    for key in ("require_stop", "require_basis", "max_price_deviation",
                "context_budget"):
        assert re.search(rf"^  {key}:", yaml_src, re.M), \
            f"config.yaml 缺 agent.{key} 键"


def test_load_config_roundtrip(tmp_path, monkeypatch):
    """端到端：yaml 写非常规值 → load_config → TradingBridge 不丢值。"""
    import yaml as _yaml

    from paper_trading.agent import AgentConfig
    from paper_trading.utils.config import load_config

    custom = {"enabled": True, "require_stop": True, "require_basis": True,
              "max_price_deviation": 0.5, "news_limit": 7,
              "context_budget": 5555}
    cfg_path = tmp_path / "config.yaml"
    base = _yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    base["agent"].update(custom)
    cfg_path.write_text(_yaml.safe_dump(base, allow_unicode=True),
                        encoding="utf-8")
    out = load_config(str(cfg_path))
    ag = out["agent"]
    for k, v in custom.items():
        assert ag[k] == v, f"load_config 丢了 agent.{k}：{ag.get(k)} != {v}"

    # 构造层不丢：模拟 cli 的取值方式
    assert bool(ag.get("require_stop", False)) is True
    assert float(ag.get("max_price_deviation", 0.30)) == 0.5
    assert int(ag.get("context_budget", 8000)) == 5555


def test_bridge_constructs_agent_config_with_values(tmp_path):
    """TradingBridge 实测：自定义值真的进了 AgentConfig 实例（双层穿透）。"""
    import shutil

    from paper_trading.cli import TradingBridge

    import yaml as _yaml

    cfg_path = tmp_path / "config.yaml"
    base = _yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    base["agent"].update({"require_stop": True, "max_price_deviation": 0.5,
                          "news_limit": 7, "context_budget": 5555})
    cfg_path.write_text(_yaml.safe_dump(base, allow_unicode=True),
                        encoding="utf-8")
    b = TradingBridge(data_db=str(tmp_path / "d.db"),
                      account_db=str(tmp_path / "a.db"),
                      config_path=str(cfg_path))
    assert b.agent_cfg.require_stop is True
    assert b.agent_cfg.max_price_deviation == 0.5
    assert b.agent_cfg.news_limit == 7
    assert b.agent_cfg.context_budget == 5555
