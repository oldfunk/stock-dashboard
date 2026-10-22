"""交易日历：周末 + 节假日 + 可缓存的交易所日历。

优先级：

1. `var/trading_calendar.json`（由 `calendar sync` 从 akshare 同步，可靠源）
2. 若日期落在缓存覆盖区间内：严格按缓存判断（能识别工作日节假日）
3. 缓存不存在/未覆盖该日期：回退为「周一~周五，且不在 config.holidays」
4. 任何缓存读写/解析失败：继续用回退口径，绝不阻断交易主流程

缓存文件路径可用环境变量 `PAPER_TRADING_CALENDAR` 覆盖（测试/临时环境）。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Set

CALENDAR_ENV = "PAPER_TRADING_CALENDAR"
DEFAULT_CALENDAR_REL = Path("var") / "trading_calendar.json"
_CACHE: dict = {"path": "", "mtime_ns": -1, "data": {}}


def as_date(d: date | datetime | str) -> date:
    """归一化为 date（接受 date/datetime/ISO 字符串）。"""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.fromisoformat(d[:10]).date()
    return d


_as_date = as_date  # 内部别名，保持兼容


def calendar_path(path: Optional[str | Path] = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get(CALENDAR_ENV)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / DEFAULT_CALENDAR_REL


def _read_calendar(path: Optional[str | Path] = None) -> dict:
    """读缓存（内存缓存按 mtime 失效）；文件不存在/损坏返回空 dict。"""
    p = calendar_path(path)
    try:
        st = p.stat()
    except OSError:
        return {}
    key = str(p.resolve())
    if _CACHE.get("path") == key and _CACHE.get("mtime_ns") == st.st_mtime_ns:
        return _CACHE.get("data") or {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        dates = sorted({str(x)[:10] for x in raw.get("dates", [])
                        if str(x).strip()})
        if not dates:
            return {}
        data = {
            "dates": set(dates),
            "min_date": raw.get("min_date") or dates[0],
            "max_date": raw.get("max_date") or dates[-1],
            "source": str(raw.get("source") or ""),
            "fetched_at": str(raw.get("fetched_at") or ""),
            "path": key,
        }
        _CACHE.update(path=key, mtime_ns=st.st_mtime_ns, data=data)
        return data
    except Exception:
        return {}


def clear_calendar_cache() -> None:
    """测试/运维强制重读缓存文件。"""
    _CACHE.update(path="", mtime_ns=-1, data={})


def save_calendar(dates: Iterable[str | date | datetime],
                  source: str = "manual",
                  path: Optional[str | Path] = None,
                  fetched_at: Optional[str] = None) -> dict:
    """原子写入日历缓存；日期自动排序去重。"""
    norm: set[str] = set()
    for d in dates:
        try:
            norm.add(as_date(d).isoformat())
        except (TypeError, ValueError):
            continue
    if not norm:
        raise ValueError("交易日历为空，拒绝写入")
    rows = sorted(norm)
    p = calendar_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(source or "manual"),
        "fetched_at": fetched_at or datetime.now().isoformat(timespec="seconds"),
        "min_date": rows[0],
        "max_date": rows[-1],
        "dates": rows,
    }
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(p)
    clear_calendar_cache()
    return {"path": str(p), "n": len(rows), "min_date": rows[0],
            "max_date": rows[-1], "source": payload["source"],
            "fetched_at": payload["fetched_at"]}


def sync_calendar(path: Optional[str | Path] = None) -> dict:
    """从 akshare（新浪交易日历）同步并缓存；网络/依赖失败返回 ok=False。"""
    try:
        import akshare as ak  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"akshare 不可用：{type(e).__name__}: {e}"}
    try:
        df = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        dates = [str(x)[:10] for x in df[col].tolist()]
        info = save_calendar(dates, source="akshare.tool_trade_date_hist_sina",
                             path=path)
        return {"ok": True, **info}
    except Exception as e:  # noqa: BLE001
        return {"ok": False,
                "error": f"交易日历同步失败：{type(e).__name__}: {e}"}


def calendar_status(path: Optional[str | Path] = None) -> dict:
    """缓存覆盖范围/新鲜度，供 CLI/面板展示。"""
    data = _read_calendar(path)
    if not data:
        return {"available": False, "path": str(calendar_path(path)),
                "source": "", "min_date": "", "max_date": "",
                "covers_today": False, "needs_sync": True}
    today = date.today().isoformat()
    max_date = str(data["max_date"])
    try:
        need_after = (date.today() + timedelta(days=45)).isoformat()
        needs_sync = max_date < need_after
    except Exception:
        needs_sync = False
    return {"available": True, "path": data["path"],
            "source": data["source"], "fetched_at": data["fetched_at"],
            "min_date": data["min_date"], "max_date": max_date,
            "n": len(data["dates"]),
            "covers_today": data["min_date"] <= today <= max_date,
            "needs_sync": bool(needs_sync)}


def is_trading_day(d: date | datetime | str, holidays: Optional[Set[str]] = None) -> bool:
    """是否为交易日。

    缓存覆盖该日期时以缓存为准（工作日节假日会被识别）；否则回退周末+holidays。
    """
    dd = _as_date(d)
    if dd.weekday() >= 5:
        return False
    iso = dd.isoformat()
    if holidays and iso in holidays:
        return False
    data = _read_calendar()
    if data and data["min_date"] <= iso <= data["max_date"]:
        return iso in data["dates"]
    return True


def next_trading_day(
    d: date | datetime | str, holidays: Optional[Set[str]] = None, steps: int = 1
) -> date:
    """下一个交易日（steps=1 即 T+1 解冻日）。"""
    dd = _as_date(d)
    n = 0
    while n < steps:
        dd += timedelta(days=1)
        if is_trading_day(dd, holidays):
            n += 1
    return dd


def load_holidays(extra: Optional[Iterable[str]] = None) -> Set[str]:
    """合并外部节假日列表为集合，容错空值。"""
    out: Set[str] = set()
    if extra:
        for h in extra:
            h = str(h).strip()[:10]
            if h:
                out.add(h)
    return out
