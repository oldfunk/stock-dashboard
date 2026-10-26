"""研究仓储接口（2026-10-26，R0-2a）。

- ``ResearchRepository``：只读查询 + 研究写入（特征/质量/成分）。
- ``SqliteResearchRepository``：读业务库 + 写研究表（写只进研究表，不碰业务表）。
- ``ParquetResearchRepository``：只读 Parquet 快照（DuckDB）。

原则：研究查询不直接写业务库；写入走研究仓储，且 Parquet 快照只读。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Iterable

from src.models.database import db_conn
from src.research.parquet import SnapshotReader


class RepositoryError(RuntimeError):
    """仓储用法错误（如向只读快照写入、向业务表写入）。"""


# 研究仓储只允许写这些表；其余（业务表）一律拒绝。
RESEARCH_TABLES = frozenset({
    "market", "instrument", "trading_calendar", "fx_rate_daily",
    "feature_definition", "stock_feature_daily", "universe_membership",
    "data_quality_daily", "research_hypothesis", "research_experiment",
    "research_result", "strategy_version", "strategy_deployment",
    "strategy_attribution", "decision_log", "prompt_registry", "llm_run",
})

_TABLE_RE = re.compile(
    r"^\s*(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)"
    r"\s+([`\"\[]?[\w.]+[`\"\]]?)", re.IGNORECASE)


class ResearchRepository(ABC):
    name: str = "abstract"

    @abstractmethod
    def query(self, sql: str, params: tuple | list | None = None) -> list[dict]:
        """只读查询，返回行字典列表。"""

    @abstractmethod
    def execute(self, sql: str, params: tuple | list | None = None) -> int:
        """研究写入，返回受影响行数。"""


class SqliteResearchRepository(ResearchRepository):
    name = "sqlite"

    def __init__(self, allowed_tables: Iterable[str] | None = None):
        self.allowed_tables = frozenset(allowed_tables or RESEARCH_TABLES)

    def query(self, sql: str, params: tuple | list | None = None) -> list[dict]:
        with db_conn() as conn:
            cursor = conn.execute(sql, params or [])
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def execute(self, sql: str, params: tuple | list | None = None) -> int:
        self._guard(sql)
        with db_conn() as conn:
            return conn.execute(sql, params or []).rowcount

    def _guard(self, sql: str) -> None:
        match = _TABLE_RE.match(sql)
        if not match:
            raise RepositoryError(f"无法识别写目标表，拒绝（fail-closed）: {sql[:60]!r}")
        table = match.group(1).strip('`"[]')
        if table not in self.allowed_tables:
            raise RepositoryError(
                f"研究仓储只写研究表，拒绝写业务表: {table}")


class ParquetResearchRepository(ResearchRepository):
    """只读 Parquet 快照仓储（DuckDB）。"""

    name = "parquet"

    def __init__(self, snapshot_id: str):
        self.snapshot_id = snapshot_id
        self._reader = SnapshotReader(snapshot_id)

    def query(self, sql: str, params: tuple | list | None = None) -> list[dict]:
        return self._reader.query(sql, params)

    def execute(self, sql: str, params: tuple | list | None = None) -> int:
        raise RepositoryError(
            "Parquet 快照只读：写入请用 SqliteResearchRepository")


# ── 常用只读查询 ────────────────────────────────────────────────────────────

def get_prices(repo: ResearchRepository, start_date: str | None = None,
               end_date: str | None = None, field: str = "close_adj") -> list[dict]:
    """前复权价格（含 instrument_id 回退，兼容迁移前后）。"""
    sql = (f"SELECT COALESCE(instrument_id, 'CN:' || code) AS iid, "
           f"trade_date, {field} AS value FROM kline_daily "
           f"WHERE {field} IS NOT NULL")
    params: list = []
    if start_date:
        sql += " AND trade_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND trade_date <= ?"
        params.append(end_date)
    sql += " ORDER BY iid, trade_date"
    return repo.query(sql, params)


def get_features(repo: ResearchRepository, feature_id: str,
                 trade_date: str | None = None,
                 feature_version: int = 1) -> list[dict]:
    """特征值（按品种）；缺值不返回。"""
    sql = ("SELECT instrument_id, trade_date, value FROM stock_feature_daily "
           "WHERE feature_id = ? AND feature_version = ? AND value IS NOT NULL")
    params: list = [feature_id, feature_version]
    if trade_date:
        sql += " AND trade_date = ?"
        params.append(trade_date)
    sql += " ORDER BY instrument_id"
    return repo.query(sql, params)
