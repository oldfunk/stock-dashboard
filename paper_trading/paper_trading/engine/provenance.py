"""回测可复现指纹（M2，docs/ROADMAP.md §2）：结果必须绑定数据快照与参数。

同一 `bars_hash` + 同一 `params_hash` ⇒ 同一实验（同指标是可验证的推论，
不是巧合）；任一变化 ⇒ 新实验，旧结论不得直接套用。哈希截断 16 位 hex
（内部对照足够；碰撞在本项目量级下不构成风险）。

- ``bars_hash``   日线快照：{symbol|timestamp|o|h|l|c|volume} 逐行规范化后 sha256
- ``minutes_hash`` 分钟线快照（日内规则回测时才有）
- ``days_hash``   交易日列表（同 bars 不同窗口 = 不同实验）
- ``params_hash`` 参数集：规则集/费用/滑点/风控/初始资金，canonical JSON 后 sha256

``spec`` 是指纹算法版本；算法变更必须 bump（旧指纹不可比要显式）。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Optional

SPEC = "fp-v1"


def _sha16(lines: Iterable[str]) -> str:
    h = hashlib.sha256()
    for line in lines:
        h.update(str(line).encode("utf-8"))
        h.update(b"\n")
    return "sha256:" + h.hexdigest()[:16]


def _num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "nan"
    if not math.isfinite(f):
        return "nan"
    return f"{f:.6f}"


def _f(obj: Any, name: str):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def params_fingerprint(params: Any) -> str:
    """参数集指纹（canonical JSON：键排序/紧凑分隔/非序列化对象 str 化）。"""
    canon = json.dumps(params, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), default=str)
    return _sha16([canon])


def bars_fingerprint(bars_by_sym: dict[str, list],
                     days: Optional[list] = None,
                     minutes_by_day: Optional[dict] = None) -> dict:
    """数据快照指纹。bars 支持 Bar 对象或 dict；行序无关（排序后规范化）。"""
    lines: list[str] = []
    for sym in sorted(bars_by_sym or {}):
        rows = list(bars_by_sym.get(sym) or [])
        rows.sort(key=lambda b: str(_f(b, "timestamp") or _f(b, "date") or ""))
        for b in rows:
            ts = str(_f(b, "timestamp") or _f(b, "date") or "")[:19]
            lines.append("|".join([
                str(sym), ts,
                _num(_f(b, "open")), _num(_f(b, "high")),
                _num(_f(b, "low")), _num(_f(b, "close")),
                str(int(_f(b, "volume") or 0)),
            ]))
    out: dict = {"spec": SPEC, "bars_hash": _sha16(lines),
                 "n_bars": len(lines), "n_symbols": len(bars_by_sym or {})}
    if minutes_by_day:
        mlines: list[str] = []
        for key in sorted((minutes_by_day or {}).keys(), key=str):
            for row in minutes_by_day.get(key) or []:
                # row: (ts, o, h, l, c, v, a?) 按序；只取前 6 个字段
                vals = list(row)[:6]
                while len(vals) < 6:
                    vals.append(None)
                mlines.append(
                    f"{key}|{str(vals[0])[:19]}|{_num(vals[1])}|{_num(vals[2])}"
                    f"|{_num(vals[3])}|{_num(vals[4])}|{int(vals[5] or 0)}")
        if mlines:
            out["minutes_hash"] = _sha16(mlines)
            out["n_minutes"] = len(mlines)
    if days:
        d = [str(x)[:10] for x in days]
        out["days_hash"] = _sha16(d)
        out["period"] = {"start": d[0], "end": d[-1], "n_days": len(d)}
    return out


def backtest_fingerprint(*, bars_by_sym: dict, days: list, cfg: Any,
                         fee: Any, slippage_pct: float, slippage_fixed: float,
                         risk_cfg: Optional[dict], initial_cash: float,
                         minutes_by_day: Optional[dict] = None) -> dict:
    """数据 + 参数合成指纹（Backtester.run 与 CLI 共用同一口径）。"""
    fp = bars_fingerprint(bars_by_sym, days, minutes_by_day)
    fp["params_hash"] = params_fingerprint({
        "cfg": cfg,
        "fee": {"commission_rate": getattr(fee, "commission_rate", None),
                "commission_min": getattr(fee, "commission_min", None),
                "stamp_duty_rate": getattr(fee, "stamp_duty_rate", None),
                "transfer_fee_rate": getattr(fee, "transfer_fee_rate", None)},
        "slippage_pct": float(slippage_pct or 0.0),
        "slippage_fixed": float(slippage_fixed or 0.0),
        "risk": dict(risk_cfg or {}) or None,
        "initial_cash": float(initial_cash),
    })
    return fp
