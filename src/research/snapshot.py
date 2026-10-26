"""研究快照 manifest 与版本指纹（2026-10-26，R0-4）。

目的：实验可复现。manifest 记录：
- snapshot_id / 创建时间
- code_commit / config_hash / random_seed（调用方注入）
- 每个研究所用表的行数、最大日期、列 schema hash、统计指纹
- DB 文件大小

说明：当前 SQLite 阶段不做全量数据 hash（大表代价高）；Parquet 阶段将改为
逐文件 sha256。本 manifest 的 fingerprint 是"结构 + 关键统计"指纹，
用于发现快照被改动；完整复现仍需数据文件/快照目录。

注：``db_size``/``schema_hash`` 为**信息性**字段，不参与校验——DB 会因
无关写入而变大，若参与校验会让快照平白失效。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from src.models import database as _db
from src.models.database import db_conn, init_database, now_cn

DATE_COLUMNS = (
    "trade_date", "report_date", "snapshot_date", "decision_date",
    "period_end", "date",
)


class SnapshotError(ValueError):
    """快照缺失、损坏或不一致。"""


@dataclass
class TableStat:
    table: str
    rows: int = 0
    max_date: str | None = None
    columns_hash: str = ""
    fingerprint: str = ""


@dataclass
class SnapshotManifest:
    snapshot_id: str
    created_at: str
    code_commit: str | None
    config_hash: str | None
    random_seed: int | None
    db_size: int
    schema_hash: str
    tables: dict[str, TableStat] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tables"] = {k: asdict(v) for k, v in self.tables.items()}
        return d


def _snapshots_dir() -> Path:
    # 动态取路径（模块属性），保证测试 monkeypatch get_db_path 生效
    return Path(_db.get_db_path()).parent.parent / "snapshots"


def _repo_root() -> Path:
    # DB 路径: <repo>/data/db/stock_dashboard.db
    return Path(_db.get_db_path()).parent.parent.parent


def code_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_repo_root()), capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 - 非 git 环境允许缺失
        pass
    return None


def config_hash() -> str | None:
    files = [_repo_root() / "config" / "config.yaml",
             _repo_root() / "config" / "strategies.yaml"]
    hasher = hashlib.sha256()
    found = False
    for path in files:
        if path.exists():
            found = True
            hasher.update(path.read_bytes())
    return hasher.hexdigest() if found else None


def table_stat(table: str) -> TableStat:
    with db_conn() as conn:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not cols:
            raise SnapshotError(f"表不存在: {table}")
        rows = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        max_date = None
        for col in DATE_COLUMNS:
            if col in cols:
                row = conn.execute(
                    f"SELECT MAX({col}) AS d FROM {table}").fetchone()
                max_date = row["d"]
                break
    columns_hash = hashlib.sha256(
        ",".join(sorted(cols)).encode()).hexdigest()
    fingerprint = hashlib.sha256(
        f"{table}:{rows}:{max_date}:{columns_hash}".encode()).hexdigest()
    return TableStat(table=table, rows=rows, max_date=max_date,
                      columns_hash=columns_hash, fingerprint=fingerprint)


def create_snapshot(tables: Iterable[str], snapshot_id: str | None = None,
                    random_seed: int | None = None, notes: str = "") -> SnapshotManifest:
    """创建快照 manifest（SQLite 阶段为结构 + 统计指纹）。"""
    init_database()
    if snapshot_id is None:
        snapshot_id = "DS-" + now_cn().strftime("%Y%m%d-%H%M%S")
    tables = list(dict.fromkeys(tables))
    stats = {t: table_stat(t) for t in tables}
    schema_hash = hashlib.sha256(
        json.dumps({t: s.fingerprint for t, s in sorted(stats.items())},
                   sort_keys=True).encode()).hexdigest()
    db_path = Path(_db.get_db_path())
    manifest = SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=now_cn().isoformat(),
        code_commit=code_commit(),
        config_hash=config_hash(),
        random_seed=random_seed,
        db_size=db_path.stat().st_size if db_path.exists() else 0,
        schema_hash=schema_hash,
        tables=stats,
        notes=notes,
    )
    out_dir = _snapshots_dir() / snapshot_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    return manifest


def verify_snapshot(snapshot_id: str) -> SnapshotManifest:
    """校验快照是否仍与 manifest 一致；不一致抛 SnapshotError。"""
    init_database()
    path = _snapshots_dir() / snapshot_id / "manifest.json"
    if not path.exists():
        raise SnapshotError(f"快照不存在: {snapshot_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest = SnapshotManifest(
        snapshot_id=data["snapshot_id"], created_at=data["created_at"],
        code_commit=data.get("code_commit"), config_hash=data.get("config_hash"),
        random_seed=data.get("random_seed"), db_size=data.get("db_size", 0),
        schema_hash=data["schema_hash"], notes=data.get("notes", ""))
    manifest.tables = {
        k: TableStat(**v) for k, v in data["tables"].items()}
    for table, expected in manifest.tables.items():
        actual = table_stat(table)
        if (actual.rows != expected.rows
                or actual.max_date != expected.max_date
                or actual.columns_hash != expected.columns_hash):
            raise SnapshotError(
                f"快照 {snapshot_id} 表 {table} 已变化（行数/日期/结构）")
    return manifest


def list_snapshots() -> list[str]:
    root = _snapshots_dir()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir()
                  if (p / "manifest.json").exists())
