"""母项目路径解析。"""
from __future__ import annotations

import os
from pathlib import Path


def mother_dir(explicit: str | None = None) -> Path | None:
    """母项目根目录；不存在返回 None（调用方回退独立模式）。"""
    cand = explicit or os.environ.get("STOCK_DASHBOARD_DIR") or "~/stock-dashboard"
    p = Path(cand).expanduser()
    if not p.is_dir():
        return None
    if not (p / "src").is_dir():
        return None
    return p


def mother_db_path(mdir: Path | None = None) -> Path | None:
    """母库 stock_dashboard.db 路径；不可读返回 None。"""
    md = mdir or mother_dir()
    if md is None:
        return None
    db = md / "data" / "db" / "stock_dashboard.db"
    if not db.is_file():
        return None
    return db


def is_merged(explicit: str | None = None) -> bool:
    """是否处于合并工作状态（母库可读）。"""
    return mother_db_path(mother_dir(explicit)) is not None
