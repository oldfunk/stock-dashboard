"""Research OS 包（2026-10-26，R0 框架）。

子模块：domain（领域对象/ID/状态机）、schema（研究表）、dao（研究账本）、
registry（注册校验）、snapshot（快照 manifest）、quality（数据质量门）、
experiment（实验接口/回测适配器）、decision（决策账本）、
parquet（Parquet 快照 + DuckDB 分析）、repository（研究仓储接口）。
"""

__all__ = [
    "domain", "schema", "dao", "registry", "snapshot", "quality",
    "experiment", "decision", "parquet", "repository",
]
