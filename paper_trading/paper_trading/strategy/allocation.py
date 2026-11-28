"""多策略资金分配（I5c-3 / A3）：配置、额度、统一入口。

设计边界（`docs/ai-autonomy.md`）：
- 框架只提供**能力**：权重由用户/AI 在 `strategy.allocations` 里显式配置，
  代码里没有默认权重、没有"哪类策略更优"的偏好；
- 空配置 = 退回单方案行为（零行为变化）；
- 非法配置 fail-closed：类型错、权重 ≤0、权重和 >100%、未知策略名一律报错，
  不用默认值静默兜底；
- 额度口径：`weight × 当前 NAV` 为某策略的**累计净买入成本额度**。
  `AllocationBook` 在成交后累加买入金额、卖出时按标的归属回冲；
  这是可审计的近似（成本口径，不是逐日盯市市值），面板/CLI 会明确标注；
  同一标的先后归属不同策略时，卖出按**最近一次买入**归属回冲（近似，
  组合层真实风控仍由账户级 RiskManager 按全账户计算）；
- 卖出永远不受分配额度限制（止损不能被预算卡住）；
- 分配层只做"额度"，撮合与账户级风控仍走同一个 `RiskManager` + 账本。

配置形状：

```yaml
strategy:
  allocations:
    ma_cross: 0.6
    rsi_zone: 0.4
```

`strategy.local.yaml` 的同名键优先（与策略列表一致，本文件不进 git）。
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Mapping, Optional


class AllocationError(ValueError):
    """分配配置非法（调用方必须停线/报错，不许静默回退）。"""


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError as exc:                   # 配置存在但读不了 = 不可判定
        raise AllocationError("缺少 pyyaml，无法读取资金分配配置") from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:                     # noqa: BLE001 - 坏 YAML 要停线
        raise AllocationError(
            f"{path.name} 解析失败：{type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise AllocationError(f"{path.name} 顶层必须是对象")
    return data


def validate_allocations(raw) -> dict[str, float]:
    """校验权重表；空/None 返回 {}（=未启用分配）。任何非法都抛 AllocationError。"""
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise AllocationError(f"strategy.allocations 必须是对象：{raw!r}")
    out: dict[str, float] = {}
    for key, val in raw.items():
        if not isinstance(key, str):
            raise AllocationError(
                f"strategy.allocations 的策略名必须是字符串：{key!r}")
        name = str(key or "").strip()
        if not name:
            raise AllocationError("strategy.allocations 含空策略名")
        if name in out:
            raise AllocationError(f"strategy.allocations 策略名重复：{name}")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise AllocationError(f"{name} 的权重必须是数字：{val!r}")
        w = float(val)
        if not math.isfinite(w):
            raise AllocationError(f"{name} 的权重不是有限数：{val!r}")
        if w <= 0 or w > 1:
            raise AllocationError(f"{name} 的权重须在 (0, 1]：{val!r}")
        out[name] = w
    total = sum(out.values())
    if total > 1.0 + 1e-9:
        raise AllocationError(
            f"strategy.allocations 权重和 {total:.4f} 超过 100%"
            "（现金/风控余量必须保留，不做满仓硬分配）")
    return out


def load_allocations(config_path: str | Path = "config.yaml",
                     root: str | Path = ".") -> dict[str, float]:
    """`strategy.local.yaml` 优先，其次 `config.yaml`；空 = 未启用。"""
    local = _read_yaml(Path(root) / "strategy.local.yaml")
    if "allocations" in local:
        raw = local.get("allocations")
    else:
        raw = (_read_yaml(Path(config_path)).get("strategy") or {}).get("allocations")
    return validate_allocations(raw)


def check_names(weights: Mapping[str, float], names: list[str]) -> None:
    """权重里的策略名必须在已构建策略里；未知 = fail-closed。"""
    if not weights:
        return
    known = {str(n) for n in names}
    unknown = sorted(set(weights) - known)
    if unknown:
        raise AllocationError(
            f"strategy.allocations 引用了未启用/未知策略 {unknown}"
            f"（当前策略：{sorted(known)}）")


def budget(weight: float, nav_total: float) -> float:
    """单策略累计净买入成本额度。"""
    try:
        nav = float(nav_total)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(nav) or nav <= 0:
        return 0.0
    return max(0.0, float(weight) * nav)


class AllocationBook:
    """按策略累计已用额度（account db 持久化，成本口径）。

    表：
    - `strategy_alloc_usage(strategy PK, used_notional, updated_at)`
    - `strategy_alloc_pos(symbol PK, strategy, updated_at)`：卖出回冲时找归属。
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_alloc_usage (
                    strategy TEXT PRIMARY KEY,
                    used_notional REAL NOT NULL DEFAULT 0,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_alloc_pos (
                    symbol TEXT PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    updated_at TEXT
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def used(self, strategy: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT used_notional FROM strategy_alloc_usage WHERE strategy = ?",
                (strategy,)).fetchone()
        try:
            return max(0.0, float(row["used_notional"])) if row else 0.0
        except (TypeError, ValueError):
            return 0.0

    def record_buy(self, strategy: str, symbol: str, notional: float) -> None:
        val = max(0.0, float(notional or 0.0))
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO strategy_alloc_usage(strategy, used_notional, updated_at)"
                " VALUES (?, ?, ?) ON CONFLICT(strategy) DO UPDATE SET"
                " used_notional = used_notional + excluded.used_notional,"
                " updated_at = excluded.updated_at",
                (strategy, val, now))
            conn.execute(
                "INSERT INTO strategy_alloc_pos(symbol, strategy, updated_at)"
                " VALUES (?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET"
                " strategy = excluded.strategy, updated_at = excluded.updated_at",
                (symbol, strategy, now))

    def record_sell(self, symbol: str, notional: float) -> Optional[str]:
        """按标的归属回冲卖出金额；返回归属策略（无归属返回 None）。"""
        val = max(0.0, float(notional or 0.0))
        with self._connect() as conn:
            row = conn.execute(
                "SELECT strategy FROM strategy_alloc_pos WHERE symbol = ?",
                (symbol,)).fetchone()
            if not row:
                return None
            strategy = str(row["strategy"])
            conn.execute(
                "UPDATE strategy_alloc_usage"
                " SET used_notional = MAX(0, used_notional - ?), updated_at = ?"
                " WHERE strategy = ?",
                (val, datetime.now().isoformat(), strategy))
        return strategy

    def snapshot(self) -> dict[str, float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT strategy, used_notional FROM strategy_alloc_usage").fetchall()
        out: dict[str, float] = {}
        for r in rows:
            try:
                out[str(r["strategy"])] = max(0.0, float(r["used_notional"]))
            except (TypeError, ValueError):
                continue
        return out


def check_buy(weights: Mapping[str, float], book: AllocationBook, *,
              strategy: str, order_value: float, nav_total: float,
              run_spent: Mapping[str, float] | None = None) -> tuple[bool, str]:
    """买入前的统一额度闸：未配置=放行（旧行为）；配置后缺/错策略名一律拒。"""
    if not weights:
        return True, ""
    name = str(strategy or "").strip()
    if not name:
        return False, ("未标注策略（strategy.allocations 已启用，"
                       "买入必须给 strategy）")
    if name not in weights:
        return False, f"策略 {name} 未分配额度（允许：{sorted(weights)}）"
    cap = budget(weights[name], nav_total)
    spent = float((run_spent or {}).get(name, 0.0) or 0.0)
    used = book.used(name) + spent
    try:
        val = float(order_value or 0.0)
    except (TypeError, ValueError):
        return False, "买入金额非法"
    if not math.isfinite(val) or val <= 0:
        return False, "买入金额非法"
    if used + val > cap + 1e-6:
        return False, (f"超策略 {name} 资金额度：已用 {used:.0f} + 本单 {val:.0f} "
                       f"> 额度 {cap:.0f}（{weights[name]:.0%} × NAV）")
    return True, ""


def allocations_payload(weights: Mapping[str, float], book: Optional[AllocationBook],
                        nav_total: float) -> dict:
    """给上下文/CLI 的只读快照；空权重返回 enabled=False。"""
    if not weights:
        return {"enabled": False,
                "note": "未配置 strategy.allocations；沿用单方案行为"}
    used = book.snapshot() if book is not None else {}
    items = []
    for name, w in sorted(weights.items()):
        cap = budget(w, nav_total)
        u = float(used.get(name, 0.0))
        items.append({"strategy": name, "weight": round(float(w), 6),
                      "budget": round(cap, 2), "used": round(u, 2),
                      "remaining": round(max(0.0, cap - u), 2)})
    return {"enabled": True, "nav_total": round(float(nav_total or 0.0), 2),
            "total_weight": round(sum(weights.values()), 6),
            "items": items,
            "basis": "累计净买入成本额度（weight × NAV；卖出按标的归属回冲）",
            "note": "额度只约束新买入；卖出/持有不受限；同标的跨策略换手按最近买入归属回冲"}
