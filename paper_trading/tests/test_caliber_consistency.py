"""撮合口径只许有一份：把 AGENTS.md 硬规则第 1 条从人的纪律变成机器断言。

硬规则原文：「撮合参数（费率/涨跌幅/最小档位）只允许改 config.yaml，
不许在代码里写死第二套口径」。但代码里确实存在几套硬编码默认值：

- models/types.py 的 TradingConfig dataclass 默认 7 项费率
- engine/backtest.py 的 FeeModel dataclass 默认 4 项费率
- utils/config.py 的 load_config fallback

其中最危险的是 `Backtester.__init__` 的 `self.fee = fee or FeeModel()`：
生产路径（cli / dashboard）都从 config.yaml 喂参没问题，但 optimize /
直接构造 Backtester 而不传 fee 的路径会静默吃掉 FeeModel 的硬编码默认，
一旦有人改了 config.yaml 却忘了这些默认值，回测口径就与实盘悄悄分叉，
且不报错、直接污染 NAV 可比性。

本文件锁死「代码里的默认口径 == config.yaml 口径」，让分叉在 CI 变红。

自证它会失败（故意让代码默认 != config.yaml）：
    python -m pytest tests/test_caliber_consistency.py -q   # 基线：12 passed
    # 临时改 types.py commission_rate 默认 0.00025 -> 0.0003
    python -m pytest tests/test_caliber_consistency.py -q   # 变红，点名 commission_rate
"""
from __future__ import annotations

from pathlib import Path

import pytest

from paper_trading.engine.backtest import FeeModel
from paper_trading.models import TradingConfig
from paper_trading.utils.config import load_config

# config.yaml 必须按仓库根的绝对路径读：conftest._isolated_cwd 会把每个用例
# chdir 到 tmp_path，用相对路径会读不到真相源。
CONFIG_YAML = Path(__file__).resolve().parents[1] / "config.yaml"

# 7 项撮合/滑点口径（config.yaml trading: 段的真相源键）
TRADING_KEYS = [
    "commission_rate",
    "commission_min",
    "stamp_duty_rate",
    "transfer_fee_rate",
    "slippage_fixed",
    "slippage_pct",
    "use_slippage_pct",
]

# FeeModel 只承载 4 项费率（无滑点字段）
FEE_KEYS = [
    "commission_rate",
    "commission_min",
    "stamp_duty_rate",
    "transfer_fee_rate",
]


def _yaml_trading() -> dict:
    """从仓库根 config.yaml 读 trading: 段的原始口径（真相源）。"""
    import yaml

    raw = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8")) or {}
    return raw.get("trading") or {}


def test_load_config_returns_yaml_trading_values():
    """loader 不被改坏：load_config 读回的口径 == config.yaml 原始值。"""
    yaml_t = _yaml_trading()
    loaded = load_config(CONFIG_YAML)["trading_config"]
    for key in TRADING_KEYS:
        assert getattr(loaded, key) == yaml_t[key], (
            f"load_config.{key}={getattr(loaded, key)!r} != config.yaml {yaml_t[key]!r}"
        )


@pytest.mark.parametrize("key", TRADING_KEYS)
def test_trading_config_defaults_match_yaml(key):
    """TradingConfig dataclass 默认值 == config.yaml 同项值。

    这些默认值是潜在的「第二套口径」：一旦和 config.yaml 分叉，
    任何无参构造 TradingConfig 的路径都会静默用错口径。
    """
    yaml_t = _yaml_trading()
    code_default = getattr(TradingConfig(), key)
    assert code_default == yaml_t[key], (
        f"{key}: TradingConfig 代码默认 {code_default!r} != config.yaml {yaml_t[key]!r}"
        f"（改口径必须同步 config.yaml 与代码默认，否则回测/实盘口径分叉）"
    )


@pytest.mark.parametrize("key", FEE_KEYS)
def test_fee_model_defaults_match_yaml(key):
    """FeeModel dataclass 默认值 == config.yaml 同项值。

    这是最高危的一处：`Backtester.__init__` 的 `self.fee = fee or FeeModel()`
    会在调用方不传 fee 时静默吃掉这些硬编码默认，而它们从不读 config.yaml。
    """
    yaml_t = _yaml_trading()
    code_default = getattr(FeeModel(), key)
    assert code_default == yaml_t[key], (
        f"{key}: FeeModel 代码默认 {code_default!r} != config.yaml {yaml_t[key]!r}"
        f"（Backtester 不传 fee 时会静默用这套默认，口径分叉直接污染回测）"
    )
