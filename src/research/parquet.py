"""Parquet 研究快照与 DuckDB 分析（2026-10-26，R0-2b/R0-2c）。

- ``export_snapshot``：SQLite 表 -> ``data/snapshots/<id>/parquet/<table>.parquet``，
  写 manifest（表名/行数/最大日期/文件 sha256）；可重跑（覆盖重写）。
- ``verify_snapshot``：逐文件 sha256 校验；不一致抛 ``ParquetError``。
- ``SnapshotReader``：DuckDB **只读** Parquet 视图 + ``query()``。
- ``scan_forward_returns``：研究查询函数（回测扫描示例）。

依赖：``duckdb``（生产已装 1.5.6；sqlite 扩展首次需联网下载后缓存）。
路径布局与 R0-4 的 SQLite 阶段 manifest 分离：
``data/snapshots/<id>/manifest.json``（R0-4）与
``data/snapshots/<id>/parquet/parquet-manifest.json``（本模块）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from src.models import database as _db
from src.models.database import now_cn

try:
    import duckdb
except ImportError:  # pragma: no cover - 依赖缺失时给出清晰错误
    duckdb = None


class ParquetError(RuntimeError):
    """快照缺失/损坏或 DuckDB 不可用。"""


def _require_duckdb() -> None:
    if duckdb is None:
        raise ParquetError("duckdb 未安装：.venv/bin/pip install 'duckdb>=1.1,<2'")


def snapshots_dir() -> Path:
    return Path(_db.get_db_path()).parent.parent / "snapshots"


def parquet_dir(snapshot_id: str) -> Path:
    return snapshots_dir() / snapshot_id / "parquet"


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _posix(path: Path) -> str:
    return path.as_posix()


@dataclass
class ParquetTable:
    table: str
    rows: int
    max_date: str | None
    file: str
    sha256: str
    size: int


@dataclass
class ParquetManifest:
    snapshot_id: str
    created_at: str
    duckdb_version: str
    tables: dict[str, ParquetTable] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tables"] = {k: asdict(v) for k, v in self.tables.items()}
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ParquetManifest":
        manifest = cls(snapshot_id=data["snapshot_id"],
                       created_at=data["created_at"],
                       duckdb_version=data.get("duckdb_version", ""))
        manifest.tables = {k: ParquetTable(**v)
                           for k, v in data.get("tables", {}).items()}
        return manifest


def _connect_source(con, db_path: str) -> None:
    """DuckDB 只读挂载 SQLite 业务库（sqlite 扩展首次需下载）。"""
    try:
        con.execute("LOAD sqlite;")
    except Exception:  # noqa: BLE001 - 未安装时安装后再加载
        con.execute("INSTALL sqlite;")
        con.execute("LOAD sqlite;")
    con.execute(f"ATTACH '{db_path}' AS _src (TYPE SQLITE, READ_ONLY);")


def _max_date(con, dest: Path) -> str | None:
    columns = [d[0] for d in
               con.execute(f"SELECT * FROM read_parquet('{_posix(dest)}') LIMIT 0")
               .description]
    for column in ("trade_date", "report_date", "snapshot_date", "date"):
        if column in columns:
            row = con.execute(
                f"SELECT MAX({column}) FROM read_parquet('{_posix(dest)}')"
            ).fetchone()
            return row[0]
    return None


def export_snapshot(snapshot_id: str, tables: Iterable[str]) -> ParquetManifest:
    """把 SQLite 表导出为 Parquet 快照并写 manifest（可重跑：覆盖重写）。"""
    _require_duckdb()
    out_dir = parquet_dir(snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        _connect_source(con, _db.get_db_path())
        stats: dict[str, ParquetTable] = {}
        for table in dict.fromkeys(tables):
            dest = out_dir / f"{table}.parquet"
            con.execute(
                f"COPY (SELECT * FROM _src.{table}) TO '{_posix(dest)}' "
                f"(FORMAT PARQUET)")
            rows = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{_posix(dest)}')"
            ).fetchone()[0]
            stats[table] = ParquetTable(
                table=table, rows=int(rows), max_date=_max_date(con, dest),
                file=dest.name, sha256=_sha256(dest), size=dest.stat().st_size)
        manifest = ParquetManifest(
            snapshot_id=snapshot_id, created_at=now_cn().isoformat(),
            duckdb_version=duckdb.__version__, tables=stats)
        (out_dir / "parquet-manifest.json").write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return manifest
    finally:
        con.close()


def verify_snapshot(snapshot_id: str) -> ParquetManifest:
    """逐文件 sha256 校验；缺失/不符抛 ``ParquetError``。"""
    path = parquet_dir(snapshot_id) / "parquet-manifest.json"
    if not path.exists():
        raise ParquetError(f"Parquet 快照不存在: {snapshot_id}")
    manifest = ParquetManifest.from_dict(
        json.loads(path.read_text(encoding="utf-8")))
    for table, stat in manifest.tables.items():
        file = parquet_dir(snapshot_id) / stat.file
        if not file.exists():
            raise ParquetError(f"Parquet 文件缺失: {table}")
        if _sha256(file) != stat.sha256:
            raise ParquetError(f"Parquet 文件哈希不符: {table}")
    return manifest


class SnapshotReader:
    """DuckDB 只读 Parquet 的研究查询入口。"""

    def __init__(self, snapshot_id: str):
        _require_duckdb()
        self.snapshot_id = snapshot_id
        self.manifest = verify_snapshot(snapshot_id)
        self.dir = parquet_dir(snapshot_id)
        self._con = duckdb.connect()
        for table in self.manifest.tables:
            self._con.execute(
                f"CREATE VIEW {table} AS SELECT * FROM "
                f"read_parquet('{_posix(self.dir / (table + '.parquet'))}')")

    def query(self, sql: str, params: tuple | list | None = None) -> list[dict]:
        cursor = self._con.execute(sql, params or [])
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "SnapshotReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def scan_forward_returns(reader: SnapshotReader, start_date: str,
                         end_date: str, horizon_days: int,
                         topk: int = 5) -> list[dict]:
    """回测扫描：每只品种在 start 持有 horizon_days 个交易日的前向收益，取 top-k。

    缺失 entry/exit 价格的品种被跳过（不补零）。
    """
    rows = reader.query(
        "SELECT COALESCE(instrument_id, 'CN:' || code) AS iid, trade_date, "
        "close_adj FROM kline_daily "
        "WHERE close_adj IS NOT NULL AND trade_date BETWEEN ? AND ? "
        "ORDER BY iid, trade_date", (start_date, end_date))
    series: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        series.setdefault(row["iid"], []).append(
            (row["trade_date"], row["close_adj"]))
    out: list[dict] = []
    for iid, points in series.items():
        if len(points) <= horizon_days:
            continue
        entry = points[0][1]
        exit_ = points[horizon_days][1]
        if entry <= 0:
            continue
        out.append({"instrument_id": iid,
                    "forward_return": round(exit_ / entry - 1.0, 6)})
    out.sort(key=lambda item: -item["forward_return"])
    return out[:topk]
