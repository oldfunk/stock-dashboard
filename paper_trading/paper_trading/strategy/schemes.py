"""投资方案：永远只有 3 种。

1. mother   Stock Dashboard 价值投资理念方案（需检测到 Stock Dashboard 才可用；
            选股范围=母筛选，执行沿用通用规则，论点优先）
2. general  Paper Trading 默认通用交易策略（默认选中，开箱即用）
3. custom   自定义：用户用自然语言写交易指令，直接喂给 AI

用户自选只写 gitignored 的 `strategy.local.yaml`（active + instruction），
git 树永远干净。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

LOCAL_FILE = "strategy.local.yaml"

MOTHER_ID = "mother"
GENERAL_ID = "general"
CUSTOM_ID = "custom"

SCHEME_IDS = (MOTHER_ID, GENERAL_ID, CUSTOM_ID)


@dataclass
class Scheme:
    name: str
    title: str = ""
    desc: str = ""
    available: bool = True
    source: str = ""  # 方案来源标识：mother / general / custom（面板分组与 CLI 展示用）
    allow_buy: bool = True  # 是否允许买入（展示用；风控另行钳制）
    universe_source: str = "config"  # config | watchlist | screening | all
    universe_tag: str = ""  # 预留：screening 按母策略 tag 过滤（现缺省不过滤）
    universe_limit: int = 20
    signal: dict = field(default_factory=dict)  # short_window/long_window/buy_volume/sell_volume
    sizing: dict = field(default_factory=dict)  # max_orders_per_run/max_order_value
    risk: dict = field(default_factory=dict)  # daily_loss_halt_pct
    exits_note: str = ""
    instruction: str = ""  # 仅 custom：用户自然语言交易指令


def _read_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _mother_ok() -> bool:
    from paper_trading.integration.settings import is_merged

    try:
        return bool(is_merged())
    except Exception:
        return False


def builtin_schemes() -> dict[str, Scheme]:
    """通用默认方案（永远可用，开箱即用）。"""
    return {
        GENERAL_ID: Scheme(
            name=GENERAL_ID, title="通用交易",
            desc="Paper Trading 默认策略：MA5/20 趋势跟踪，配置池内决策",
            available=True, source=GENERAL_ID, universe_source="config",
            signal={"short_window": 5, "long_window": 20,
                    "buy_volume": 100, "sell_volume": 100},
            exits_note="MA 死叉离场；论点卖出条件优先于技术信号",
        ),
    }


def mother_scheme() -> Scheme | None:
    """Stock Dashboard 价值方案：Stock Dashboard 可读时才存在，否则返回 None。"""
    if not _mother_ok():
        return None
    return Scheme(
        name=MOTHER_ID, title="Stock Dashboard 价值",
        desc="Stock Dashboard 价值投资理念：选股范围取自 Stock Dashboard 最新筛选，执行沿用通用规则，论点优先",
        available=True, source=MOTHER_ID, universe_source="screening", universe_limit=15,
        signal={"short_window": 5, "long_window": 20,
                "buy_volume": 100, "sell_volume": 100},
        sizing={"max_orders_per_run": 2, "max_order_value": 15000.0},
        exits_note="论点卖出条件一票否决；无明确机会就持有不动",
    )


def custom_instruction(root: str | Path = ".") -> str:
    """用户自然语言交易指令（strategy.local.yaml）。"""
    raw = _read_yaml(Path(root) / LOCAL_FILE)
    return str(raw.get("instruction", "") or "")


def all_schemes(root: str | Path = ".") -> dict[str, Scheme]:
    """全量三类（custom 永远在列，instruction 可能为空）。"""
    d = builtin_schemes()
    m = mother_scheme()
    if m is not None:
        d[MOTHER_ID] = m
    d[CUSTOM_ID] = Scheme(
        name=CUSTOM_ID, title="自定义指令",
        desc="你用自然语言写交易策略，AI 照此执行（风控钳制不变）",
        available=True, source=CUSTOM_ID, universe_source="config",
        signal={"short_window": 5, "long_window": 20,
                "buy_volume": 100, "sell_volume": 100},
        exits_note="以你的指令为准；指令冲突处置：风控与合规优先",
        instruction=custom_instruction(root),
    )
    return d


def active_name(root: str | Path = ".", default: str = GENERAL_ID) -> tuple[str, str]:
    """当前选中方案名；返回 (name, 来源)。CLI > strategy.local.yaml > 缺省。"""
    raw = _read_yaml(Path(root) / LOCAL_FILE)
    name = str(raw.get("active", "") or default)
    src = "local" if raw.get("active") else "default"
    return name, src


def resolve_scheme(name: str, root: str | Path = ".") -> Scheme:
    """按名取方案；未知或母方案不可用（不在列表里）时回退通用默认。"""
    schemes = all_schemes(root)
    return schemes.get(name) or schemes[GENERAL_ID]


def set_active(root: str | Path, name: str,
               instruction: str | None = None) -> tuple[bool, str]:
    """切换方案（只写 gitignored 的 strategy.local.yaml）。

    - 仅接受 mother/general/custom 三个 id
    - mother 在母项目不可读时拒绝（报错明确，不静默回退）
    - instruction 非空则一并保存为自定义指令
    """
    from paper_trading.strategy.schemes import all_schemes as _all

    root_p = Path(root)
    if name not in (MOTHER_ID, GENERAL_ID, CUSTOM_ID):
        return False, f"未知方案（仅支持 {MOTHER_ID}/{GENERAL_ID}/{CUSTOM_ID}）：{name}"
    if name == MOTHER_ID and name not in _all(root_p):
        return False, "Stock Dashboard 不在同一台机器，母价值方案不可用"
    try:
        import yaml  # type: ignore
    except ImportError:
        return False, "缺少 pyyaml"
    p = root_p / LOCAL_FILE
    raw: dict = {}
    if p.exists():
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
    raw["active"] = name
    if instruction is not None:
        raw["instruction"] = str(instruction)[:2000]
    try:
        p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return True, f"已切换为 {name}"
    except Exception as e:
        return False, f"写入失败：{e}"


def mother_strategies(mother: str | Path | None = None) -> list[dict]:
    """Stock Dashboard strategies.yaml 只读展示（选股侧语言，供参照）。"""
    from paper_trading.integration.settings import mother_dir

    md = mother_dir(str(mother) if mother else None)
    if md is None:
        return []
    raw = _read_yaml(md / "config" / "strategies.yaml")
    out = []
    for key, v in raw.items():
        if not isinstance(v, dict):
            continue
        out.append({"key": key, "name": v.get("name", key),
                    "desc": v.get("desc", ""),
                    "n_rules": len([k for k in v if k not in ("name", "desc")])})
    return out
