"""生产心跳与看门狗（T22）——把"静默故障"变成可见告警。

背景：Pi 连续自重启无人发现，靠用户肉眼看出（`docs/migration-nec.md` 记录）。
本模块只做两件事：

1. **心跳**：每轮成功跑完后，往 `var/heartbeat.json` 记一条 `{name: {ts, ok, detail}}`。
   写在轮次**末尾** ⇒ 进程中途被 SIGKILL/断电/崩溃时不会跳动 ⇒ 看门狗能看见"没跑成"
   而不是"跑挂了但假装跑成"。
2. **看门狗**：读心跳年龄 + 面板 systemd 活性，超阈值即
   ① 写 `var/health_status.json`（面板据此显示红条，恢复后自动清空）
   ② 仅在**状态翻转**（健康→告警 / 告警→恢复）时落一条 `op_log[data:health]`，
      避免每分钟刷流水（与 `engine_triggers` 的"同 key 每天一条"同一精神）。

零外部依赖：只用标准库 + 本地文件 + `systemctl` 本机命令；不碰外网。
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

HEARTBEAT_REL = Path("var") / "heartbeat.json"
STATUS_REL = Path("var") / "health_status.json"

#: 阈值（秒）。engine 是分钟级 cron ⇒ 5 分钟没跳就是挂了。
DEFAULT_THRESHOLDS = {"engine": 300, "agent": 64800}
#: rq_tick：M3 执行线的消费跳（cron `2-59/5`），15 分钟没跳=新线没在消费队列。
#: **不进 DEFAULT_THRESHOLDS**——没切换的部署不该被它假红；由 verify/health 在
#: executor=rqalpha 时显式加上（分层与 startup_guard 一致）。
RQ_TICK_THRESHOLD = 900

#: minute：盘中分钟线采集器（`minute sync --intraday`）。新线撮合只认库里的 bar，
#: 采集器停了 = 意图没有 bar 可撮合、隔日过期——机会被静默吞掉。同样只在切换后要求。
MINUTE_THRESHOLD = 900

#: 只在"本该跳"的时间里判停滞，否则周末/夜里天天误报——告警疲劳是 fail-loud 的头号杀手。
#: rq_tick：交易日 09:02–15:04（cron `2-59/5 9-15`；收盘后不要求跳）
RQ_TICK_DUE = (9 * 60 + 2, 15 * 60 + 4)
#: minute：交易日 09:35–15:05（第一根完整 5 分钟 bar 在 09:35 之后才有）
MINUTE_DUE = (9 * 60 + 35, 15 * 60 + 5)
#: engine：交易日 09:00–15:59（cron `* 9-15 * * 1-5`）
#: agent ：交易日收盘计划窗口结束后（默认 16:30 起，给 4h 容错到 20:30 才追问"今天没跑成"）
ENGINE_DUE = (9 * 60, 15 * 60 + 59)
AGENT_DUE_AFTER_MIN = 16 * 60 + 30 + 240      # 20:30

#: 名字 → 当日应跳窗口（分钟）。看门狗判"第一跳迟到了多久"时要用窗口起点，
#: 不能直接拿"距上次心跳"——跨夜后的第一次检查必然看到十几小时的年龄。
DUE_WINDOWS = {"engine": ENGINE_DUE, "rq_tick": RQ_TICK_DUE, "minute": MINUTE_DUE}

#: 面板服务的两种可能形态（systemd --user 或系统级 unit），逐个探测，任一 active 即算活着
DASHBOARD_UNITS = (
    # nec 生产实际是系统级 unit（/etc/systemd/system/），不是 Pi 时代的 user unit
    "paper-trading-dashboard.service",
    "paper-trading-parent.service",
    "paper-dashboard.service",
    "paper-dashboard",
)
SYSTEMCTL_PROBES = (
    ["systemctl", "is-active"],
    ["systemctl", "--user", "is-active"],
)


def default_path(rel: Path = HEARTBEAT_REL) -> Path:
    """默认落在仓库目录下的 var/（cron 的 cwd 就是仓库根，与 calendar 缓存同惯例）。"""
    return Path(rel)


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now()


def load(path: Optional[Path] = None) -> dict:
    """读心跳表；文件缺失/损坏一律回退空 dict（看门狗自己不能因脏文件而崩）。"""
    p = Path(path) if path else default_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def touch(name: str, *, ok: bool = True, detail: str = "",
          path: Optional[Path] = None, now: Optional[datetime] = None) -> bool:
    """记录一次心跳（原子写）。**永不抛错**——心跳件本身不许拖垮交易轮次。"""
    p = Path(path) if path else default_path()
    data = load(p)
    data[name] = {"ts": _now(now).isoformat(timespec="seconds"),
                 "ok": bool(ok), "detail": str(detail or "")[:200]}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / (p.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def age(name: str, path: Optional[Path] = None,
        now: Optional[datetime] = None) -> Optional[float]:
    """距上次心跳的秒数；无记录/时间戳损坏返回 None（None 一律按"不健康"处理）。"""
    rec = load(path).get(name)
    if not isinstance(rec, dict):
        return None
    try:
        ts = datetime.fromisoformat(str(rec.get("ts")))
    except (TypeError, ValueError):
        return None
    return (_now(now) - ts).total_seconds()


def due(name: str, now: Optional[datetime] = None) -> bool:
    """该名字现在"本该跳"吗？非应跳时段一律不判停滞（避免周末/夜间误报）。"""
    d = _now(now)
    try:
        from paper_trading.utils.trading_calendar import is_trading_day

        if not is_trading_day(d.date()):
            return False
    except Exception:                                   # noqa: BLE001 - 日历不可用则保守照判
        pass
    mins = d.hour * 60 + d.minute
    if name == "engine":
        return ENGINE_DUE[0] <= mins <= ENGINE_DUE[1]
    if name == "rq_tick":
        return RQ_TICK_DUE[0] <= mins <= RQ_TICK_DUE[1]
    if name == "minute":
        return MINUTE_DUE[0] <= mins <= MINUTE_DUE[1]
    if name == "agent":
        return mins >= AGENT_DUE_AFTER_MIN
    return True


def _seconds_since_due_start(name: str, now: datetime) -> Optional[float]:
    """距今天该心跳项"应跳窗口起点"的秒数；无窗口概念返回 None。

    窗口第一跳的宽限（2026-11-18 事故派生）：engine cron 在 09:00:00 起跳，
    但心跳写在轮次末尾；看门狗 09:00:02 就检查时，最后一条心跳还是昨天
    15:59 —— 直接拿"距上次心跳"判，就是每天固定一次"engine 应跳未跳"
    （现网 11-19 09:00 实测 61,259s）。按窗口起点算，第一跳只迟到 2 秒。
    """
    if name == "agent":
        start_min = AGENT_DUE_AFTER_MIN
    else:
        win = DUE_WINDOWS.get(name)
        if not win:
            return None
        start_min = win[0]
    start = now.replace(hour=start_min // 60, minute=start_min % 60,
                        second=0, microsecond=0)
    return (now - start).total_seconds()


def probe(thresholds: Optional[dict] = None, path: Optional[Path] = None,
          now: Optional[datetime] = None) -> dict:
    """心跳体检：返回 {checks, unhealthy, healthy}。

    应跳而未跳 / 缺记录 = 不健康（fail-closed：没有证据就是没跑）；
    非应跳时段记为 `not-due`，不计入告警。

    `ok` 用**有效年龄**判：`min(距上次心跳, 距窗口起点)`。跨夜/周末后的
    第一次检查因此不会误报（窗口内真停滞仍会被"距窗口起点"抓住）。
    `age_sec` 保留真实年龄供人看，`since_due_sec` 是判定口径。
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    now_dt = _now(now)
    checks: dict = {}
    bad: list = []
    for name, limit in th.items():
        a = age(name, path=path, now=now)
        if not due(name, now):
            checks[name] = {"age_sec": None if a is None else round(a, 1),
                            "since_due_sec": None,
                            "limit_sec": float(limit), "ok": True, "state": "not-due"}
            continue
        since_due = _seconds_since_due_start(name, now_dt)
        eff = a
        if a is not None and since_due is not None:
            eff = min(a, since_due)
        ok = eff is not None and 0 <= eff <= float(limit)
        checks[name] = {"age_sec": None if a is None else round(a, 1),
                        "since_due_sec": (None if since_due is None
                                          else round(since_due, 1)),
                        "limit_sec": float(limit), "ok": bool(ok),
                        "state": "ok" if ok else "stale"}
        if not ok:
            bad.append(name)
    return {"checks": checks, "unhealthy": bad, "healthy": not bad,
            "checked_at": now_dt.isoformat(timespec="seconds")}


def dashboard_active(runner=subprocess.run) -> str:
    """面板 systemd 活性：'active' / 'inactive' / 'unknown'。绝不抛错。

    cron 环境常缺 `XDG_RUNTIME_DIR`（user unit 就读不到），所以两种 unit 形态都试。
    `runner` 可注入，便于单测。
    """
    seen_any = False
    for base in SYSTEMCTL_PROBES:
        for unit in DASHBOARD_UNITS:
            try:
                r = runner(base + [unit], capture_output=True, text=True, timeout=8)
            except Exception:                               # noqa: BLE001 - 探测失败不算故障
                continue
            seen_any = True
            if (r.stdout or "").strip() == "active":
                return "active"
    return "inactive" if seen_any else "unknown"


def check(thresholds: Optional[dict] = None, path: Optional[Path] = None,
          now: Optional[datetime] = None, runner=subprocess.run,
          status_path: Optional[Path] = None) -> dict:
    """看门狗一次完整检查：心跳 + 面板活性，并落状态文件供面板显示红条。

    返回 {"healthy","checks","dashboard","alerts":[...] }；`alerts` 是要显示的中文告警。
    """
    out = probe(thresholds=thresholds, path=path, now=now)
    now_dt = _now(now)
    dash = dashboard_active(runner=runner)
    alerts = [f"{n} 应跳未跳（最后心跳 "
              + (f"{int(c['age_sec'])}s 前" if c["age_sec"] is not None else "无记录")
              + f"，阈值 {int(c['limit_sec'])}s）"
              for n, c in out["checks"].items() if not c["ok"]]
    if dash == "inactive":
        alerts.append("面板服务未运行（systemd unit 非 active）")
    elif dash == "unknown":
        alerts.append("面板服务状态无法判定（缺 systemctl 或 user 会话不可见）")
    healthy = out["healthy"] and dash != "inactive"
    status = {"healthy": healthy, "alerts": alerts, "dashboard": dash,
              "checks": out["checks"], "checked_at": now_dt.isoformat(timespec="seconds")}
    write_status(status, path=status_path)
    return status


def write_status(status: dict, path: Optional[Path] = None) -> bool:
    p = Path(path) if path else default_path(STATUS_REL)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / (p.name + ".tmp")
        tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def read_status(path: Optional[Path] = None,
                max_age_sec: Optional[float] = None) -> dict:
    """读心跳快照；`max_age_sec` 给出时效判据（Q11，2026-11-12）。

    此前无时效判据：文件存在就永远返回旧快照 ⇒ 面板健康区恒绿——观察期
    一场 4 天停摆（389 次 fail-closed 拒单）全程被掩盖。超龄返回 `{}`，
    调用方（dashboard `/api/health`）即落 `probe()` 实时判。
    """
    p = Path(path) if path else default_path(STATUS_REL)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    if max_age_sec is not None:
        try:
            from datetime import datetime
            checked = datetime.fromisoformat(str(d.get("checked_at") or ""))
            if (datetime.now() - checked).total_seconds() > float(max_age_sec):
                return {}
        except Exception:              # noqa: BLE001 - 时间戳坏了同按无效
            return {}
    return d


def transitioned(prev: dict, cur: dict) -> Optional[str]:
    """状态翻转判定：只在 健康↔告警 之间变化时返回事件名（防流水刷屏）。"""
    was = bool((prev or {}).get("healthy", True))
    now_ok = bool((cur or {}).get("healthy", True))
    if was and not now_ok:
        return "degraded"
    if (not was) and now_ok:
        return "recovered"
    return None
