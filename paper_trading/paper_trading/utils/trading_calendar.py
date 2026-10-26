"""交易日历：面向**未来**的交易日窗口（周末 + 节假日）。

缓存要回答的是“今天/明天/下一交易日是不是交易日”——服务收盘计划的目标日、
T+1 解冻日、实时交易时段判断。历史交易日与这些判断无关，所以 `calendar sync`
只保存「最近 `HISTORY_TAIL_DAYS` 天（近况查询兜底）+ 源里全部可得的未来交易日」，
不存 1990 年以来的全量历史。

优先级：

1. `var/trading_calendar.json`（由 `calendar sync` 从 akshare 同步，可靠源）
2. 若日期落在缓存覆盖区间内：严格按缓存判断（能识别工作日节假日）
3. 缓存不存在/未覆盖该日期：回退为「周一~周五，且不在 config.holidays」
4. 任何缓存读写/解析失败：继续用回退口径，绝不阻断交易主流程

缓存文件路径可用环境变量 `PAPER_TRADING_CALENDAR` 覆盖（测试/临时环境）。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Set

CALENDAR_ENV = "PAPER_TRADING_CALENDAR"
DEFAULT_CALENDAR_REL = Path("var") / "trading_calendar.json"
#: 同步时保留的最近历史天数：只为“最近一个假期/节后审计”这类近况查询兜底，
#: 不是存历史档案；缓存主体是未来交易日（更早历史要查就查数据源，别信缓存）。
HISTORY_TAIL_DAYS = 45
_CACHE: dict = {"path": "", "mtime_ns": -1, "data": {}}
_LOGGER = logging.getLogger(__name__)
_HORIZON_WARNED: Set[str] = set()


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


def sync_calendar(path: Optional[str | Path] = None,
                  timeout_sec: float = 30.0) -> dict:
    """从 akshare（新浪交易日历）同步**未来**交易日并缓存；失败返回 ok=False。

    接口虽叫 hist，返回的是“历史 + 未来”的全量交易所日历；我们只留
    「最近 HISTORY_TAIL_DAYS 天 + 未来全部」，历史部分不入库。

    `timeout_sec`：akshare 没有逐请求 timeout，连不上会 stall；超时按失败
    返回，绝不挂住 cron（否则会长期持有 run_lock）。
    """
    try:
        import akshare as ak  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"akshare 不可用：{type(e).__name__}: {e}"}
    from .timeout import call_with_timeout

    try:
        df = call_with_timeout(ak.tool_trade_date_hist_sina, timeout_sec)
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        cutoff = (date.today() - timedelta(days=HISTORY_TAIL_DAYS)).isoformat()
        dates = [d for d in (str(x)[:10] for x in df[col].tolist()) if d >= cutoff]
        info = save_calendar(dates, source="akshare.tool_trade_date_hist_sina",
                             path=path)
        return {"ok": True, "history_tail_days": HISTORY_TAIL_DAYS, **info}
    except Exception as e:  # noqa: BLE001
        return {"ok": False,
                "error": f"交易日历同步失败：{type(e).__name__}: {e}"}


def calendar_status(path: Optional[str | Path] = None) -> dict:
    """缓存覆盖范围/新鲜度（未来优先），供 CLI/面板展示。

    `future_days`/`lookahead_days` 回答“未来还能看多远”，是同步是否该跑的依据。
    """
    data = _read_calendar(path)
    if not data:
        return {"available": False, "path": str(calendar_path(path)),
                "source": "", "min_date": "", "max_date": "", "n": 0,
                "future_days": 0, "lookahead_days": None,
                "covers_today": False, "needs_sync": True, "stale": True}
    today_d = date.today()
    today = today_d.isoformat()
    min_date = str(data["min_date"])
    max_date = str(data["max_date"])
    future_days = sum(1 for d in data["dates"] if d > today)
    try:
        lookahead_days = (date.fromisoformat(max_date) - today_d).days
    except Exception:                             # noqa: BLE001
        lookahead_days = None
    try:
        stale = (datetime.now()
                 - datetime.fromisoformat(str(data["fetched_at"]))).days > 14
    except Exception:                             # noqa: BLE001
        stale = False
    needs_sync = (lookahead_days is not None and lookahead_days < 45) or stale
    return {"available": True, "path": data["path"],
            "source": data["source"], "fetched_at": data["fetched_at"],
            "min_date": min_date, "max_date": max_date,
            "n": len(data["dates"]), "future_days": future_days,
            "lookahead_days": lookahead_days,
            "covers_today": min_date <= today <= max_date,
            "needs_sync": bool(needs_sync), "stale": bool(stale)}


def calendar_covers(d: date | datetime | str,
                    path: Optional[str | Path] = None) -> bool:
    """交易日历缓存是否覆盖该日期（覆盖不到 = 无法确认交易日）。

    - 缓存不存在：返回 True。首次部署/源不可用时维持“周末+holidays”回退，
      否则系统会完全停摆（此时 `is_trading_day` 仍会给出保守判断）。
    - 有缓存但日期晚于 `max_date`（如 2027 源尚未发布）：返回 False，
      交易入口（tick/引擎/事件/实时报价）应 **fail-closed** 跳过，
      并提示跑 `calendar sync`；禁止把“未知日”当交易日静默成交。
    """
    data = _read_calendar(path)
    if not data:
        return True
    return _as_date(d).isoformat() <= str(data["max_date"])


def is_trading_day(d: date | datetime | str, holidays: Optional[Set[str]] = None) -> bool:
    """是否为交易日。

    缓存覆盖该日期时以缓存为准（工作日节假日会被识别）；否则回退周末+holidays。
    日期晚于缓存 `max_date`（如 2027 年源尚未发布）时按工作日回退，但会告警
    一次，避免“假期被当交易日”完全无声（运维应尽快 `calendar sync`）。
    """
    dd = _as_date(d)
    if dd.weekday() >= 5:
        return False
    iso = dd.isoformat()
    if holidays and iso in holidays:
        return False
    data = _read_calendar()
    if data and data["min_date"] <= iso:
        if iso <= data["max_date"]:
            return iso in data["dates"]
        if iso not in _HORIZON_WARNED:
            _HORIZON_WARNED.add(iso)
            _LOGGER.warning(
                "交易日历缓存只覆盖到 %s，%s 按工作日回退判断；请运行 calendar sync",
                data["max_date"], iso)
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
