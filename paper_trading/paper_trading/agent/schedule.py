"""AI 定时队列：时刻 → 动作，一天按顺序消费。

cron 只负责高频 tick（如每 15 分钟叫一次 `agent tick`），本模块在进程内
判定“现在轮到哪条”：每条每天最多跑一次，时刻没到/队列跑完一律跳过
（记流水，不问 LLM）。

动作（每条固定一种）：
    sync     只同步行情+名称，不决策不下单
    analyze  走完整决策链但只分析不下单（dry-run，记流水）
    plan     休盘做计划存着，开盘执行（不碰账本）
    trade    实盘决策，可下单（风控钳制不变）

用户配置只写 gitignored 的 `strategy.local.yaml`（与投资方案同一文件）：
    agent_schedule:
      queue:
        - {time: "09:00", action: sync}
        - {time: "13:00", action: plan}
        - {time: "16:45", action: trade}

缺省队列为 [{"time": "16:45", "action": "trade"}]（收盘 15:00 +
数据源落定余量，保守时间）。
消费凭流水里的 slot 标记，不凭时间推断（双时钟错位已吃过亏）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

LOCAL_FILE = "strategy.local.yaml"

ACTIONS = ("sync", "analyze", "plan", "trade")
ACTION_CN = {"sync": "同步", "analyze": "分析", "plan": "计划", "trade": "交易"}
DEFAULT_QUEUE = [{"time": "16:45", "action": "trade"}]
MAX_ENTRIES = 5


def _now() -> datetime:
    """当前时间（单测 monkeypatch 点）。"""
    return datetime.now()


def parse_time(text: str) -> str:
    """校验单个 HH:MM，返回零填充规范形。"""
    p = str(text or "").strip()
    hh_mm = p.split(":")
    if len(hh_mm) != 2 or not all(x.isdigit() for x in hh_mm):
        raise ValueError(f"时刻格式错误（须为 HH:MM）：{text}")
    hh, mm = int(hh_mm[0]), int(hh_mm[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"时刻超出范围（00:00-23:59）：{text}")
    return f"{hh:02d}:{mm:02d}"


def parse_entry(item) -> dict:
    """解析单条：认 "16:45=trade" / {"time","action"}；裸 "16:45" 视为 trade。"""
    if isinstance(item, dict):
        t, a = item.get("time", ""), item.get("action", "trade")
    elif isinstance(item, str):
        if "=" in item:
            t, a = item.split("=", 1)
        else:
            t, a = item, "trade"
    else:
        raise ValueError(f"队列条目格式错误：{item}")
    t = parse_time(t)
    a = str(a or "trade").strip().lower()
    if a not in ACTIONS:
        raise ValueError(f"未知动作（仅支持 {','.join(ACTIONS)}）：{a}")
    return {"time": t, "action": a}


def parse_queue(items) -> list[dict]:
    """解析整表：时刻唯一（同分钟两条会打架，直接拒绝），按时间排序，最多 N 条。"""
    if isinstance(items, str):
        items = [p.strip() for p in items.replace("；", ",").replace(";", ",").split(",")
                 if p.strip()]
    if not isinstance(items, (list, tuple)) or not items:
        raise ValueError("队列至少保留一条（示例：16:45=trade）")
    out = [parse_entry(i) for i in items]
    times = [e["time"] for e in out]
    if len(set(times)) != len(times):
        raise ValueError(f"同一时刻只能出现一次：{times}")
    if len(out) > MAX_ENTRIES:
        raise ValueError(f"队列最多 {MAX_ENTRIES} 条")
    return sorted(out, key=lambda e: e["time"])


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


def _migrate_legacy(raw: dict):
    """旧格式兼容：slots: ["16:45"]（缺省 trade）→ entries；max_runs 直接忽略。"""
    slots = raw.get("slots")
    if isinstance(slots, str):
        slots = [slots]
    if isinstance(slots, (list, tuple)):
        return [{"time": parse_time(s), "action": "trade"} for s in slots if str(s).strip()]
    return None


def load_schedule(root: str | Path = ".") -> dict:
    """读定时队列；缺失/损坏一律回默认（fail-open 用默认，不阻断）。"""
    raw = _read_yaml(Path(root) / LOCAL_FILE).get("agent_schedule") or {}
    queue = raw.get("queue", raw.get("entries"))
    if queue is None:
        try:
            leg = _migrate_legacy(raw)
            queue = leg if leg else [dict(e) for e in DEFAULT_QUEUE]
        except ValueError:
            queue = [dict(e) for e in DEFAULT_QUEUE]
    try:
        entries = parse_queue(queue)
    except ValueError:
        entries = [dict(e) for e in DEFAULT_QUEUE]
    return {"entries": entries, "source": "local" if raw else "default"}


def save_schedule(root: str | Path, items) -> tuple[bool, str]:
    """保存定时队列（只写 strategy.local.yaml；校验失败不落盘）。"""
    try:
        entries = parse_queue(items)
    except ValueError as e:
        return False, str(e)
    try:
        import yaml  # type: ignore
    except ImportError:
        return False, "缺少 pyyaml"
    p = Path(root) / LOCAL_FILE
    raw: dict = _read_yaml(p)
    raw["agent_schedule"] = {"queue": entries}
    try:
        p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        desc = "、".join(f"{e['time']}{ACTION_CN[e['action']]}" for e in entries)
        return True, f"已保存队列（每日 {len(entries)} 跑）：{desc}"
    except Exception as e:
        return False, f"写入失败：{e}"


def due_entries(entries: list[dict], now: Optional[datetime] = None) -> list[dict]:
    """此刻已到达的条目（HH:MM 字符串比较即可，同为零填充）。"""
    hm = (now or _now()).strftime("%H:%M")
    return [e for e in entries if e["time"] <= hm]


def consumed_slots(times: list[str], live_times: list[str]) -> set[str]:
    """升级回退口径（仅无 slot 标记的老流水）：每次实盘消费其时刻前最近一刻。"""
    done: set[str] = set()
    for t in live_times:
        past = [s for s in times if s <= t and s not in done]
        if past:
            done.add(past[-1])
    return done


def check(entries: list[dict], fired: set[str] | list[str],
          now: Optional[datetime] = None) -> tuple[bool, dict | None, str]:
    """定时闸：返回 (放行, 条目, 原因)。

    放行最早一个已到未消费的条目（保序）；无可跑时原因 not-in-schedule
    （时刻未到，或全天队列已跑完）。fired 为今日已消费时刻集合
    （由流水中的 slot 标记得出，与时钟无关）。
    """
    done = set(fired or [])
    due = [e for e in due_entries(entries, now) if e["time"] not in done]
    if not due:
        return False, None, "not-in-schedule"
    return True, due[0], "ok"
