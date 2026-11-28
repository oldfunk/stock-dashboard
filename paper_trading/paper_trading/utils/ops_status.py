"""运维巡检（M4）：备份/心跳/行情/熔断一次看完。

定位：给 cron 和面板用的**只读聚合**，不写账本、不下单、不改状态。
- 备份：最近自动备份年龄 + 完整性；目录/阈值可配；
- 心跳：engine/agent/rq_tick/minute 应跳未跳（复用 heartbeat.probe，不写状态文件）；
- 行情：raw_gate（本地 raw 新鲜度 + 母库逐日参照翻转）；
- 熔断：RiskManager.describe 的 drawdown/daily_loss halt。

输出 `ok=false` 时 CLI 退出 1，可直接挂 cron；`var/ops_status.json` 供面板后续展示。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

OPS_STATUS_REL = Path("var") / "ops_status.json"


def _check(cid: str, name: str, status: str, detail: str,
           fix: str = "") -> dict:
    return {"id": cid, "name": name, "status": status,
            "detail": detail, "fix": fix}


def _symbols(bridge) -> list[str]:
    syms = {str(s) for s in (getattr(bridge, "stock_pool", None) or []) if s}
    try:
        syms |= {p.symbol for p in bridge.broker.get_all_positions()}
    except Exception:                       # noqa: BLE001
        pass
    return sorted(syms)


def build_status(bridge, *, backup_dir: Optional[str] = None,
                 max_backup_age_hours: float = 26.0,
                 today: Optional[str] = None,
                 check_quote: bool = True,
                 now: Optional[datetime] = None) -> dict:
    from paper_trading.utils import heartbeat as hb
    from paper_trading.utils.ledger_backup import backup_status

    checks: list[dict] = []
    day = str(today or date.today().isoformat())[:10]

    # 1) 账本备份
    bs = backup_status(bridge.account_db_path, out_dir=backup_dir,
                       max_age_hours=max_backup_age_hours, now=now)
    checks.append(_check(
        "backup", "账本自动备份",
        "green" if bs.get("ok") else "red",
        (f"最新 {bs.get('latest')}，{bs.get('age_hours')}h 前，"
         f"共 {bs.get('count')} 份，{bs.get('integrity') or '未体检'}")
        if bs.get("available") else (bs.get("reason") or "备份不可用"),
        "ledger backup --keep 20；检查 backups/ 目录与 cron"))

    # 2) 心跳（不调 check()，避免把 health_status 覆盖成非看门狗口径）
    hp = hb.probe(now=now)
    bad = hp.get("unhealthy") or []
    checks.append(_check(
        "heartbeat", "cron/守护进程心跳",
        "green" if not bad else "red",
        "全部应跳任务在阈值内" if not bad
        else "应跳未跳：" + "、".join(map(str, bad)),
        "health show / 检查 crontab 与 systemctl"))

    # 3) 行情 raw_gate（本地 raw + 母库参照）
    if check_quote:
        try:
            from paper_trading.utils.m3_verify import check_raw_gate

            syms = _symbols(bridge)
            rg = check_raw_gate(bridge.data_db, syms, day, days=60)
            level = str(rg.get("status") or "warn")
            checks.append(_check(
                "quote", "行情/raw 口径与新鲜度",
                "green" if level == "green" else
                ("red" if level == "red" else "warn"),
                str(rg.get("detail") or level),
                "data raw-coverage --gate；检查 fetch-raw cron 与母库参照"))
        except Exception as exc:            # noqa: BLE001
            checks.append(_check("quote", "行情/raw 口径与新鲜度", "warn",
                                 f"检查失败：{type(exc).__name__}: {exc}",
                                 "data raw-coverage --gate 手动复核"))
    else:
        checks.append(_check("quote", "行情/raw 口径与新鲜度", "skip",
                             "已按参数跳过"))

    # 3b) 前复权锚点/源切换（本地 vs 母库同日比值）
    if check_quote:
        try:
            from paper_trading.data.qfq_check import check as qfq_check

            q = qfq_check(bridge.data_db, _symbols(bridge), days=120)
            st = str(q.get("status") or "warn")
            checks.append(_check(
                "qfq", "前复权锚点/源切换",
                "green" if st == "green" else
                ("red" if st == "red" else ("skip" if st == "skip" else "warn")),
                (f"{len(q.get('bad') or [])} 只锚点漂移"
                 + (f"：{q.get('bad')[:3]}" if q.get("bad") else "")
                 if st == "red" else
                 ("锚点不一致：" + str((q.get("warn") or [])[:3])
                  + "；不可比：" + str((q.get("uncomparable") or [])[:3])
                  if st == "warn" else "比值稳定/无可比样本")),
                "data qfq-check --json；人工核对除权日/源，不自动改数据"))
        except Exception as exc:            # noqa: BLE001
            checks.append(_check("qfq", "前复权锚点/源切换", "warn",
                                 f"检查失败：{type(exc).__name__}: {exc}",
                                 "data qfq-check --json 手动复核"))
    else:
        checks.append(_check("qfq", "前复权锚点/源切换", "skip",
                             "已按参数跳过"))

    # 4) 熔断态（只读；不锚定日亏基线）
    try:
        st = bridge.get_status()
        rd = bridge.risk.describe(float(st.get("total_value") or 0.0))
        halts = [k for k in ("drawdown_halt", "daily_loss_halt") if rd.get(k)]
        checks.append(_check(
            "risk", "风控熔断",
            "red" if halts else "green",
            ("当前熔断：" + "、".join(halts)) if halts
            else f"未熔断；回撤 {rd.get('drawdown_pct')}% / "
                 f"日亏 {rd.get('daily_loss_pct')}%",
            "查 risk_state / ai:decide 拒因；不要手工改峰值"))
    except Exception as exc:                # noqa: BLE001
        checks.append(_check("risk", "风控熔断", "warn",
                             f"检查失败：{type(exc).__name__}: {exc}",
                             "status --json 手动复核"))

    reds = [c["id"] for c in checks if c["status"] == "red"]
    warns = [c["id"] for c in checks if c["status"] == "warn"]
    out = {"ok": not reds, "checked_at": (now or datetime.now()).isoformat(
               timespec="seconds"), "today": day,
           "account_db": str(bridge.account_db_path),
           "checks": checks, "red": reds, "warn": warns,
           "alerts": [f"{c['name']}：{c['detail']}" for c in checks
                      if c["status"] in ("red", "warn")],
           "note": "只读巡检；red 需处理，warn 需看趋势"}
    return out


def write_status(status: dict, path: Optional[str | Path] = None) -> str:
    p = Path(path) if path else OPS_STATUS_REL
    p = Path(p)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / (p.name + ".tmp")
        tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
        return str(p)
    except OSError:
        return ""


def read_status(path: Optional[str | Path] = None) -> dict:
    p = Path(path) if path else OPS_STATUS_REL
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
