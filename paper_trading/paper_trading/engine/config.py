"""引擎配置：config.yaml `engine:` 段 + gitignored 的 engine.local.yaml 覆盖。

    engine:
      enabled: false          # 总开关（默认关：显式开启才动真钱——这里是纸盘）
      interval_sec: 60        # 盘中轮询间隔
      universe: auto          # auto=程序候选池 / list=仅 rules 里点名的
      max_orders_per_day: 6   # 日内报单笔数上限
      max_turnover_per_day: 60000   # 日内成交金额上限（元）
      per_symbol_cooldown_sec: 900  # 同标的两次报单最小间隔
      min_ticket_cny: 5000    # 低于此金额不下单（手续费地板：<2万单子费率>50bp）
      rules:
        - id: trail-002192
          type: trailing_stop
          symbol: "002192"
          params: {pct: 5}
          action: {side: sell, all: true}
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from paper_trading.engine.rules import RULE_TYPES

LOCAL_FILE = "engine.local.yaml"

DEFAULTS = {
    "enabled": False,
    "interval_sec": 60,
    "universe": "auto",        # auto=规则未点名时用本地股票池；list=必须逐条点名
    "universe_max": 50,        # auto 模式最多订阅多少只（防一次拉爆行情源）
    "max_orders_per_day": 6,
    "max_turnover_per_day": 60000.0,
    "per_symbol_cooldown_sec": 900,
    "min_ticket_cny": 5000.0,
    "rules": [],
}


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


def load_engine_config(config_path: Optional[str] = None,
                       root: Optional[Path] = None) -> dict:
    """读引擎配置：默认 ← config.yaml ← engine.local.yaml（逐层覆盖）。"""
    cfg = dict(DEFAULTS)
    cfg["rules"] = []
    main = _read_yaml(Path(config_path or "config.yaml")).get("engine") or {}
    cfg.update({k: v for k, v in main.items() if k in DEFAULTS})
    base = root or Path(".")
    local_file = _read_yaml(base / LOCAL_FILE)
    local = local_file.get("engine") if isinstance(local_file, dict) else None
    if isinstance(local, dict) and local:
        cfg.update({k: v for k, v in local.items() if k in DEFAULTS})
    cfg["rules"] = [r for r in (cfg.get("rules") or []) if isinstance(r, dict)]
    return cfg


def validate(cfg: dict) -> tuple[bool, str]:
    """规则集校验：类型/动作/参数不合法直接拒绝（fail-closed）。"""
    if int(cfg.get("interval_sec") or 0) < 5:
        return False, "interval_sec 至少 5 秒"
    if int(cfg.get("max_orders_per_day") or 0) < 0:
        return False, "max_orders_per_day 不能为负"
    universe = str(cfg.get("universe") or "auto").lower()
    if universe not in ("auto", "list"):
        return False, f"universe 须为 auto/list，got {universe}"
    seen: set[str] = set()
    for i, r in enumerate(cfg.get("rules") or []):
        rid = str(r.get("id") or "")
        if not rid:
            return False, f"第 {i + 1} 条规则缺 id"
        if rid in seen:
            return False, f"规则 id 重复：{rid}"
        seen.add(rid)
        if str(r.get("type")) not in RULE_TYPES:
            return False, f"规则 {rid} 类型未知（支持 {','.join(RULE_TYPES)}）"
        syms = r.get("symbols") or ([r["symbol"]] if r.get("symbol") else [])
        if not syms and universe != "auto":
            return False, f"规则 {rid} 未指定标的（universe=list 必须点名）"
        act = r.get("action") or {}
        if str(act.get("side", "")).lower() not in ("buy", "sell"):
            return False, f"规则 {rid} action.side 须为 buy/sell"
    return True, "ok"


def save_engine_config(root: Path, cfg: dict) -> tuple[bool, str]:
    """保存到 engine.local.yaml（校验失败不落盘）。"""
    ok, msg = validate(cfg)
    if not ok:
        return False, msg
    try:
        import yaml  # type: ignore
    except ImportError:
        return False, "缺少 pyyaml"
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / LOCAL_FILE).write_text(
            yaml.safe_dump({"engine": cfg}, allow_unicode=True), encoding="utf-8")
        return True, "已保存引擎配置（engine.local.yaml）"
    except Exception as e:      # noqa: BLE001
        return False, f"写入失败：{e}"