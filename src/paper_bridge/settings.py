"""纸盘桥的实例路径解析（E0）。

默认只认**合并树**：`<repo>/paper_trading/{data.db,paper_account.db}`。
可用环境变量覆盖（测试/迁移用），但**没有**"自动发现 :8080"的分支——
独立版是另一个实例，用户约定母项目不碰。
"""
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def bridge_paths(data_db: str | os.PathLike | None = None,
                 account_db: str | os.PathLike | None = None,
                 ) -> tuple[Path, Path]:
    """返回 `(data_db, paper_account_db)` 的绝对路径。

    优先级：显式参数 > 环境变量 > 仓库默认。
    """
    root = Path(os.environ.get("PAPER_BRIDGE_ROOT") or repo_root())
    data = Path(data_db or os.environ.get("PAPER_BRIDGE_DATA_DB")
                or root / "paper_trading" / "data.db")
    account = Path(account_db or os.environ.get("PAPER_BRIDGE_ACCOUNT_DB")
                   or root / "paper_trading" / "paper_account.db")
    return data.resolve(), account.resolve()
