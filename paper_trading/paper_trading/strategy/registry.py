"""策略注册表（T87/T88/T89）：按 `{class, params}` 构造，坏配置 fail-closed。

设计约束（2026-11-20，Batch 22）：

- 内置策略只用**本地行情**，不依赖母库 / LLM / 计划；
- 参数必须过 `params_schema`（类型/范围/候选值），未知参数与越界一律拒绝，
  绝不用默认值静默兜底；
- 自定义策略只允许**已安装包**或 `PAPER_TRADING_STRATEGY_PATH` 白名单目录，
  且必须是 `BaseStrategy` 子类；策略只产 `Signal`，执行仍走风控 + 队列；
- 配置来源：`config.yaml` 的 `strategy.strategies`，`strategy.local.yaml`
  的同名键优先（本文件不进 git）。

spec 形状：

```yaml
strategy:
  strategies:
    - name: ma_cross
      class: ma_cross
      params: {short_window: 5, long_window: 20}
      enabled: true
```
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Optional

from paper_trading.strategy.base_strategy import BaseStrategy
from paper_trading.strategy.breakout_strategy import BreakoutStrategy
from paper_trading.strategy.ma_cross_strategy import MACrossStrategy
from paper_trading.strategy.rsi_strategy import RSIStrategy


class StrategyConfigError(ValueError):
    """策略配置非法（构建失败一律抛这个，调用方负责停线/留痕）。"""


BUILTINS: dict[str, type[BaseStrategy]] = {
    "ma_cross": MACrossStrategy,
    "breakout": BreakoutStrategy,
    "rsi_zone": RSIStrategy,
}

SYSTEM_PATHS = ("site-packages", "dist-packages")


def _read_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise StrategyConfigError("缺少 pyyaml，无法读取策略配置") from exc
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:                     # noqa: BLE001 - 坏 YAML 要停线
        raise StrategyConfigError(f"{p.name} 解析失败：{type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise StrategyConfigError(f"{p.name} 顶层必须是对象")
    return data


def load_specs(config_path: str | Path = "config.yaml",
               root: str | Path = ".") -> list[dict]:
    """读策略列表：`strategy.local.yaml` 优先，其次 config.yaml。

    本地文件只覆盖 `strategies` 键；`active` / `instruction` 等方案字段保持原样，
    不与 schemes.py 抢写。
    """
    local = _read_yaml(Path(root) / "strategy.local.yaml")
    if "strategies" in local:
        specs = local.get("strategies")
    else:
        specs = (_read_yaml(config_path).get("strategy") or {}).get("strategies")
        if specs is None:
            return []
    if specs is None:
        return []
    if not isinstance(specs, list):
        raise StrategyConfigError("strategies 必须是列表")
    return specs


def _check_params(cls: type[BaseStrategy], params: dict) -> dict:
    schema = getattr(cls, "params_schema", {}) or {}
    if not schema:
        # 自定义策略可以不声明 schema；params 原样传给构造函数，构造失败会报错
        return dict(params)
    unknown = [k for k in params if k not in schema]
    if unknown:
        raise StrategyConfigError(
            f"{cls.__name__} 不认识参数 {unknown}（允许：{sorted(schema)}）")
    out: dict = {}
    for key, rule in schema.items():
        if key not in params:
            continue
        val = params[key]
        typ = rule.get("type", str)
        if typ is int:
            if isinstance(val, bool) or not isinstance(val, int):
                raise StrategyConfigError(f"{key} 必须是整数：{val!r}")
        elif typ is float:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise StrategyConfigError(f"{key} 必须是数字：{val!r}")
            val = float(val)
        elif typ is str:
            if not isinstance(val, str):
                raise StrategyConfigError(f"{key} 必须是字符串：{val!r}")
        if "choices" in rule and val not in rule["choices"]:
            raise StrategyConfigError(
                f"{key} 只能取 {rule['choices']}：{val!r}")
        if rule.get("min") is not None and val < rule["min"]:
            raise StrategyConfigError(f"{key} 不能小于 {rule['min']}：{val!r}")
        if rule.get("max") is not None and val > rule["max"]:
            raise StrategyConfigError(f"{key} 不能大于 {rule['max']}：{val!r}")
        out[key] = val
    return out


def _custom_class(dotted: str) -> type[BaseStrategy]:
    """加载自定义策略类（已安装包或白名单目录）。"""
    modname, _, clsname = str(dotted).rpartition(".")
    if not modname or not clsname:
        raise StrategyConfigError(f"自定义策略必须写成 pkg.Module.Class：{dotted}")
    roots = [Path(p).expanduser().resolve()
             for p in os.environ.get("PAPER_TRADING_STRATEGY_PATH", "").split(os.pathsep)
             if p.strip()]
    added: list[str] = []
    try:
        for r in roots:
            if r.is_dir() and str(r) not in sys.path:
                sys.path.insert(0, str(r))
                added.append(str(r))
        try:
            spec = importlib.util.find_spec(modname)
        except (ImportError, ModuleNotFoundError, ValueError) as exc:
            raise StrategyConfigError(
                f"自定义策略模块 {modname} 找不到：{exc}") from exc
        if spec is None:
            raise StrategyConfigError(f"自定义策略模块 {modname} 找不到")
        module = importlib.import_module(modname)
    finally:
        for p in added:
            try:
                sys.path.remove(p)
            except ValueError:
                pass
    cls = getattr(module, clsname, None)
    if not isinstance(cls, type) or not issubclass(cls, BaseStrategy):
        raise StrategyConfigError(f"{dotted} 不是 BaseStrategy 子类")
    src = Path(getattr(module, "__file__", "") or "").resolve()
    if not src.exists():
        raise StrategyConfigError(f"{dotted} 没有可校验的源文件（内置/动态模块不可用）")
    installed = any(part in SYSTEM_PATHS for part in src.parts)
    whitelisted = any(_is_under(src, r) for r in roots)
    if not (installed or whitelisted):
        raise StrategyConfigError(
            f"{dotted} 不在白名单：只允许已安装包或 PAPER_TRADING_STRATEGY_PATH 目录")
    return cls


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def build(spec: dict) -> BaseStrategy:
    """按单个 spec 构造策略；任何非法都抛 StrategyConfigError。"""
    if not isinstance(spec, dict):
        raise StrategyConfigError(f"策略项必须是对象：{spec!r}")
    cls_name = str(spec.get("class") or "").strip()
    if not cls_name:
        raise StrategyConfigError("策略项缺 class")
    params = spec.get("params") or {}
    if not isinstance(params, dict):
        raise StrategyConfigError(f"{cls_name} 的 params 必须是对象")
    if cls_name in BUILTINS:
        cls = BUILTINS[cls_name]
    elif "." in cls_name:
        cls = _custom_class(cls_name)
    else:
        raise StrategyConfigError(
            f"未知策略类 {cls_name}（内置：{sorted(BUILTINS)}；自定义用 pkg.Class）")
    checked = _check_params(cls, params)
    try:
        inst = cls(**checked)
    except Exception as exc:                     # noqa: BLE001 - 构造失败要停线
        raise StrategyConfigError(
            f"{cls_name} 构造失败：{type(exc).__name__}: {exc}") from exc
    name = str(spec.get("name") or "").strip()
    if name:
        inst.name = name
    return inst


class CompositeStrategy(BaseStrategy):
    """多策略合并：同标的冲突时**卖出优先**（risk-off），同向取最大手数。"""

    params_schema: dict = {}

    def __init__(self, strategies: list[BaseStrategy]) -> None:
        super().__init__(name="+".join(s.name for s in strategies) or "empty")
        self.strategies = list(strategies)

    def generate_signals(self, bars):
        merged: dict = {}
        for st in self.strategies:
            for sym, sig in (st.generate_signals(bars) or {}).items():
                cur = merged.get(sym)
                if cur is None:
                    merged[sym] = sig
                    continue
                # 卖出优先；同向取最大手数（避免重复持仓/重复卖）
                sell_new = int(getattr(sig.direction, "value", 0)) == -1
                sell_cur = int(getattr(cur.direction, "value", 0)) == -1
                if sell_new and not sell_cur:
                    merged[sym] = sig
                elif sell_new and sell_cur:
                    if int(sig.volume) > int(cur.volume):
                        merged[sym] = sig
                elif not sell_new and not sell_cur:
                    if int(sig.volume) > int(cur.volume):
                        merged[sym] = sig
        return merged


def build_all(specs: list[dict]) -> BaseStrategy:
    """构造全部 enabled 策略；名字重复/任一非法都 fail-closed。"""
    if not isinstance(specs, list):
        raise StrategyConfigError("strategies 必须是列表")
    names: set[str] = set()
    built: list[BaseStrategy] = []
    for i, spec in enumerate(specs):
        if not isinstance(spec, dict):
            raise StrategyConfigError(f"第 {i+1} 个策略项不是对象")
        if spec.get("enabled", True) is False:
            continue
        name = str(spec.get("name") or spec.get("class") or f"strategy{i+1}")
        if name in names:
            raise StrategyConfigError(f"策略 name 重复：{name}")
        names.add(name)
        if not spec.get("name"):
            spec = {**spec, "name": name}
        built.append(build(spec))
    return CompositeStrategy(built)


def default_specs(defaults: Optional[dict] = None) -> list[dict]:
    """无 strategies 配置时的内置默认（MA20/60），参数来自 config 的 strategy 段。"""
    d = dict(defaults or {})
    try:
        params = {k: int(d[k]) for k in ("short_window", "long_window",
                                         "buy_volume", "sell_volume") if k in d}
    except (TypeError, ValueError) as exc:
        raise StrategyConfigError(
            f"config strategy 段参数不是整数：{exc}") from exc
    return [{"name": "ma_cross", "class": "ma_cross", "params": params,
             "enabled": True}]


def build_from_config(config_path: str | Path = "config.yaml",
                      root: str | Path = ".",
                      defaults: Optional[dict] = None) -> BaseStrategy:
    """config/local → 策略实例；空配置回内置默认。"""
    specs = load_specs(config_path, root)
    if not specs:
        specs = default_specs(defaults)
    return build_all(specs)
