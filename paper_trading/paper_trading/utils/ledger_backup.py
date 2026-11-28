"""账本备份/恢复/版本化（B1，M4）。

安全边界：
- 备份/恢复一律用 sqlite 的 **backup API**（不是文件复制）：WAL 下 `cp` 可能
  拿到半截页；backup API 产出一致快照；
- `restore` 默认只做**演练/校验**，不写目标；`apply=True` 才覆盖，且覆盖前
  自动先生成一份 `*.pre-restore.*.db` 安全副本；
- 恢复会覆盖目标账本（含 op_log），所以调用方必须先停掉面板/引擎/AI 进程；
  本模块只做文件级操作，不替你停服务；
- 版本表 `schema_meta` 记录 `schema_version`；未知更高版本拒绝启动式迁移
  （fail-closed），不猜测结构。
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

ACCOUNT_SCHEMA_VERSION = 1
#: 自动备份命名：`<stem>.<YYYYmmddTHHMMSS>.db`；安全副本/演练副本不参与轮转。
_BACKUP_RE = re.compile(r"^.+\.\d{8}T\d{6}\.db$")


def _is_primary_backup(p: Path, stem: str) -> bool:
    """自动轮转的候选：`stem.<stamp>.db`；安全副本/演练副本不算。"""
    name = p.name
    if ".pre-restore." in name or name.endswith(".drill.db"):
        return False
    return bool(_BACKUP_RE.match(name)) and name.startswith(stem + ".")


#: 账本核心表（缺任何一张都不能当成“可恢复账本”）
REQUIRED_TABLES = ("account", "positions", "orders", "fills", "nav_history",
                   "op_log", "risk_state")

#: 关键列（旧库缺列时迁移会补；备份校验只报告，不修改）
EXPECTED_COLUMNS = {
    "positions": ("symbol", "total_volume", "available_volume", "avg_cost"),
    "account": ("cash", "initial_cash"),
    "nav_history": ("timestamp", "total_value"),
    "fills": ("symbol", "direction", "volume", "price", "timestamp"),
}


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect(db_path: str | Path) -> dict:
    """只读体检：完整性、版本、表/列、行数、哈希。"""
    p = Path(db_path)
    out: dict = {"path": str(p), "exists": p.exists()}
    if not p.exists():
        out["ok"] = False
        out["error"] = "文件不存在"
        return out
    out.update({"size_bytes": p.stat().st_size, "sha256": sha256(p)})
    try:
        conn = _connect(p)
        try:
            integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {}
            for t in REQUIRED_TABLES:
                if t in tables:
                    counts[t] = conn.execute(
                        f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            missing_cols = {}
            for t, cols in EXPECTED_COLUMNS.items():
                if t not in tables:
                    continue
                have = {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}
                miss = [c for c in cols if c not in have]
                if miss:
                    missing_cols[t] = miss
            version = None
            if "schema_meta" in tables:
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()
                version = int(row[0]) if row else None
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        out.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return out
    missing_tables = sorted(set(REQUIRED_TABLES) - tables)
    out.update({
        "ok": integ == "ok" and not missing_tables,
        "integrity": integ,
        "schema_version": version,
        "target_version": ACCOUNT_SCHEMA_VERSION,
        "tables": sorted(tables),
        "missing_tables": missing_tables,
        "missing_columns": missing_cols,
        "row_counts": counts,
    })
    return out


def _copy_api(src: str | Path, dst: str | Path) -> None:
    s = sqlite3.connect(str(src))
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def _restrict(path: str | Path, mode: int) -> None:
    """POSIX 下收紧备份文件/目录权限；Windows 无 POSIX mode，忽略。"""
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _checkpoint_and_clear_wal(db_path: str | Path) -> None:
    """恢复后清掉目标残留的 -wal/-shm，防旧帧在下次打开时被重放。"""
    p = Path(db_path)
    try:
        conn = sqlite3.connect(str(p), timeout=30)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        pass
    for suffix in ("-wal", "-shm"):
        side = Path(str(p) + suffix)
        try:
            if side.exists():
                os.remove(side)
        except OSError:
            pass


def backup(db_path: str | Path, out_dir: str | Path | None = None,
           stamp: Optional[str] = None, keep: Optional[int] = None) -> dict:
    src = Path(db_path)
    if not src.exists():
        return {"ok": False, "error": f"账本不存在：{src}"}
    info = inspect(src)
    if not info.get("ok"):
        return {"ok": False, "error": "源账本体检未通过，拒绝备份",
                "inspect": info}
    stamp = stamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    d = Path(out_dir) if out_dir else src.parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"{src.stem}.{stamp}.db"
    _copy_api(src, dst)
    _restrict(dst, 0o600)
    _restrict(d, 0o700)
    chk = inspect(dst)
    if not chk.get("ok"):
        return {"ok": False, "error": "备份副本体检未通过", "backup": str(dst),
                "inspect": chk}
    out = {"ok": True, "source": str(src), "backup": str(dst),
           "size_bytes": chk["size_bytes"], "sha256": chk["sha256"],
           "row_counts": chk["row_counts"], "schema_version": chk["schema_version"]}
    if keep is not None:
        out.update(prune_backups(src, out_dir=d, keep=keep))
    return out


def prune_backups(db_path: str | Path, *, out_dir: str | Path,
                  keep: int) -> dict:
    """只删符合自动备份命名的旧文件；安全副本/演练副本永不删。

    `keep` 必须 ≥1：自动轮转最差也保留最新一份；要“全删”请人工处理。
    """
    keep = int(keep)
    if keep < 1:
        return {"pruned": [], "kept": None,
                "prune_error": f"keep 必须 ≥1（收到 {keep}）"}
    d = Path(out_dir)
    stem = Path(db_path).stem
    if not d.exists():
        return {"pruned": [], "kept": 0}
    primaries = sorted(
        [p for p in d.glob(f"{stem}.*.db") if _is_primary_backup(p, stem)],
        key=lambda p: p.name)
    old = primaries[:-keep] if len(primaries) > keep else []
    pruned = []
    for p in old:
        try:
            p.unlink()
            pruned.append(p.name)
        except OSError:
            continue
    return {"pruned": pruned, "kept": min(len(primaries), keep)}


def backup_status(db_path: str | Path, out_dir: str | Path | None = None,
                  *, max_age_hours: float = 26.0,
                  now: Optional[datetime] = None) -> dict:
    """最近一份自动备份的新鲜度/完整性；只读。"""
    src = Path(db_path)
    d = Path(out_dir) if out_dir else src.parent / "backups"
    stem = src.stem
    if not d.exists():
        return {"ok": False, "available": False, "dir": str(d),
                "reason": "备份目录不存在", "max_age_hours": max_age_hours}
    primaries = sorted(
        [p for p in d.glob(f"{stem}.*.db") if _is_primary_backup(p, stem)],
        key=lambda p: p.name)
    if not primaries:
        return {"ok": False, "available": False, "dir": str(d),
                "count": 0, "reason": "没有自动备份",
                "max_age_hours": max_age_hours}
    latest = primaries[-1]
    try:
        age_h = ((now or datetime.now()) - datetime.fromtimestamp(
            latest.stat().st_mtime)).total_seconds() / 3600.0
    except OSError as exc:
        return {"ok": False, "available": False, "dir": str(d),
                "latest": str(latest), "reason": f"{type(exc).__name__}: {exc}"}
    chk = inspect(latest)
    ok = bool(chk.get("ok")) and age_h <= float(max_age_hours)
    reason = []
    if not chk.get("ok"):
        reason.append("备份体检未通过")
    if age_h > float(max_age_hours):
        reason.append(f"备份已 {age_h:.1f}h 未更新（阈值 {max_age_hours}h）")
    return {"ok": ok, "available": True, "dir": str(d),
            "latest": latest.name, "age_hours": round(age_h, 2),
            "count": len(primaries), "size_bytes": chk.get("size_bytes"),
            "sha256": chk.get("sha256"), "integrity": chk.get("integrity"),
            "row_counts": chk.get("row_counts"),
            "schema_version": chk.get("schema_version"),
            "max_age_hours": float(max_age_hours),
            "reason": "；".join(reason) or None}



def restore(backup_path: str | Path, target_db: str | Path, *,
            apply: bool = False, out_dir: str | Path | None = None) -> dict:
    """从备份恢复；默认 dry-run（只校验 + 对比行数）。"""
    src = Path(backup_path)
    tgt = Path(target_db)
    if not src.exists():
        return {"ok": False, "error": f"备份不存在：{src}"}
    sinfo = inspect(src)
    if not sinfo.get("ok"):
        return {"ok": False, "error": "备份体检未通过，拒绝恢复",
                "inspect": sinfo}
    tinfo = inspect(tgt) if tgt.exists() else {"exists": False}
    out = {"ok": True, "applied": False, "backup": str(src),
           "target": str(tgt), "backup_inspect": sinfo,
           "target_before": tinfo,
           "note": "dry-run：未写目标；确认后加 --apply（会先自动安全备份）"}
    if not apply:
        return out
    safety = None
    if tgt.exists():
        d = Path(out_dir) if out_dir else tgt.parent / "backups"
        d.mkdir(parents=True, exist_ok=True)
        safety = d / (f"{tgt.stem}.pre-restore."
                      f"{datetime.now().strftime('%Y%m%dT%H%M%S')}.db")
        _copy_api(tgt, safety)
        _restrict(safety, 0o600)
    # 用源覆盖目标（backup API：源 → 目标连接）
    _copy_api(src, tgt)
    _checkpoint_and_clear_wal(tgt)
    after = inspect(tgt)
    if not after.get("ok"):
        return {"ok": False, "applied": True, "error": "恢复后目标体检未通过",
                "safety_backup": str(safety) if safety else None,
                "target_after": after}
    # 在恢复后的账本里留痕（恢复会覆盖原 op_log，所以必须恢复后再写）
    try:
        conn = _connect(tgt)
        try:
            conn.execute(
                "INSERT INTO op_log(timestamp, action, params, ok, result)"
                " VALUES (?, 'ledger:restore', ?, 1, ?)",
                (datetime.now().isoformat(),
                 f'{{"backup":"{src.name}","safety":"{safety.name if safety else ""}"}}',
                 f'{{"sha256":"{sinfo["sha256"]}"}}'))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        pass
    out.update({"applied": True,
                "safety_backup": str(safety) if safety else None,
                "target_after": after,
                "note": "已恢复；确认无持仓/资金异常后删除安全副本"})
    return out


def migrate(db_path: str | Path, *, apply: bool = False) -> dict:
    """账本结构版本化：缺表/缺列检查 + `schema_meta` 版本推进。

    当前只有基线 v1：建 `schema_meta`、记录版本、补 `positions.entry_day/
    peak_price`（与 broker 打开时的幂等迁移同口径）。更高版本一律拒绝。
    """
    p = Path(db_path)
    if not p.exists():
        return {"ok": False, "error": f"账本不存在：{p}"}
    info = inspect(p)
    if not info.get("ok") and info.get("missing_tables"):
        return {"ok": False, "error": "核心表缺失，拒绝自动迁移（先人工核对）",
                "missing_tables": info["missing_tables"]}
    version = info.get("schema_version")
    if version is not None and version > ACCOUNT_SCHEMA_VERSION:
        return {"ok": False,
                "error": f"账本版本 {version} 高于本程序支持的 "
                         f"{ACCOUNT_SCHEMA_VERSION}，拒绝降级迁移"}
    steps: list[str] = []
    out = {"ok": True, "applied": False, "path": str(p),
           "from_version": version, "to_version": ACCOUNT_SCHEMA_VERSION,
           "steps": steps, "inspect_before": info}
    if version == ACCOUNT_SCHEMA_VERSION and not info.get("missing_columns"):
        out["note"] = "已是最新版本，无需迁移"
        return out
    if not apply:
        if version is None:
            steps.append("create schema_meta + set schema_version=1")
        for t, cols in (info.get("missing_columns") or {}).items():
            steps.append(f"add columns {t}: {cols}")
        out["note"] = "dry-run：未写账本；确认后加 --apply"
        return out

    conn = _connect(p)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)
        if version is None:
            conn.execute(
                "INSERT INTO schema_meta(key, value, updated_at) VALUES"
                " ('schema_version', ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (str(ACCOUNT_SCHEMA_VERSION), datetime.now().isoformat()))
            steps.append("schema_version=1")
        pos_cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)")}
        if "entry_day" not in pos_cols:
            conn.execute("ALTER TABLE positions ADD COLUMN entry_day TEXT")
            steps.append("positions.entry_day")
        if "peak_price" not in pos_cols:
            conn.execute("ALTER TABLE positions ADD COLUMN peak_price REAL DEFAULT 0")
            steps.append("positions.peak_price")
        conn.execute(
            "INSERT INTO op_log(timestamp, action, params, ok, result)"
            " VALUES (?, 'ledger:migrate', ?, 1, ?)",
            (datetime.now().isoformat(),
             f'{{"from":{version},"to":{ACCOUNT_SCHEMA_VERSION}}}',
             '{{"steps":' + str(len(steps)) + '}}'))
        conn.commit()
    finally:
        conn.close()
    out.update({"applied": True, "steps": steps,
                "inspect_after": inspect(p),
                "note": "迁移完成；已完成副本演练后再上生产"})
    return out


def drill(db_path: str | Path, work_dir: str | Path) -> dict:
    """副本演练：备份 → 篡改副本 → 从备份恢复 → 校验。

    全程只碰 `work_dir` 下的副本；返回每一步结果，供运维记录/测试复用。
    """
    src = Path(db_path)
    w = Path(work_dir)
    w.mkdir(parents=True, exist_ok=True)
    step1 = backup(src, out_dir=w, stamp="drill")
    if not step1.get("ok"):
        return {"ok": False, "step": "backup", "detail": step1}
    target = w / "drill_target.db"
    if target.exists():
        target.unlink()
    _copy_api(src, target)
    # 模拟损坏：在副本里删一行 NAV（只动副本）
    try:
        conn = _connect(target)
        try:
            conn.execute("DELETE FROM nav_history WHERE id = "
                         "(SELECT MIN(id) FROM nav_history)")
            conn.commit()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        pass
    step2 = restore(step1["backup"], target, apply=True, out_dir=w)
    if not step2.get("ok"):
        return {"ok": False, "step": "restore", "detail": step2}
    a, b = step1.get("row_counts") or {}, (step2.get("target_after") or {}).get("row_counts") or {}
    ok = step2.get("target_after", {}).get("integrity") == "ok"
    return {"ok": bool(ok), "work_dir": str(w),
            "backup": step1.get("backup"),
            "safety_backup": step2.get("safety_backup"),
            "row_counts_restored": b,
            "row_counts_match": {k: (a.get(k) == b.get(k)) for k in a},
            "note": "副本演练；生产恢复前仍须停服务并人工核对钱类口径"}
