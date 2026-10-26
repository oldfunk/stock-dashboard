"""Universe 与退市名单（2026-10-26，R0-13）。

- **退市名单**：AKShare ``stock_info_sh_delist`` / ``stock_info_sz_delist``
  归一化后落 ``instrument``（status=delisted）+ ``universe_membership``。
- **每日记录**：全市场状态（in/out）、上市/退市/ST → ``universe_membership``。
- **历史成分**：免费源不完整，``coverage=current_only``；不宣称无幸存者偏差。

数据来源为注入式（行列表），便于离线测试；抓取在 ``scripts/research/``。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.models.database import db_conn, now_cn
from src.research.dao import ResearchDAO

DELISTED_UNIVERSE = "CN_DELISTED"
ALL_UNIVERSE = "CN_ALL"
COVERAGE_CURRENT_ONLY = "current_only"


class UniverseError(ValueError):
    """退市行/品种非法。"""


def normalize_delisted_row(row: dict, market: str) -> dict:
    """归一化 AKShare 退市行。

    兼容字段：``公司代码``/``code``、``公司简称``/``name``、
    ``退市日期``/``delist_date``。代码缺前导零时补齐 6 位。
    """
    code = str(row.get("公司代码") or row.get("code") or "").strip()
    name = str(row.get("公司简称") or row.get("name") or "").strip()
    delist = str(row.get("退市日期") or row.get("delist_date") or "").strip()
    if not code:
        raise UniverseError(f"退市行缺代码: {row!r}")
    if not re.fullmatch(r"\d{6}", code):
        code = code.zfill(6)
    if not re.fullmatch(r"\d{6}", code):
        raise UniverseError(f"非法股票代码: {code!r}")
    return {
        "instrument_id": f"{market}:{code}",
        "market_id": market,
        "symbol": code,
        "name": name,
        "delist_date": delist or None,
        "status": "delisted",
    }


def import_delisted(sh_rows: Iterable[dict], sz_rows: Iterable[dict],
                    dao: ResearchDAO | None = None,
                    dry_run: bool = False) -> dict:
    """把 SH/SZ 退市行写入 instrument + universe_membership。"""
    dao = dao or ResearchDAO()
    stats = {"sh": 0, "sz": 0, "errors": []}
    rows: list[dict] = []
    for market, raw, key in (("CN", sh_rows, "sh"), ("CN", sz_rows, "sz")):
        for row in raw:
            try:
                rows.append(normalize_delisted_row(row, market))
                stats[key] += 1
            except UniverseError as exc:
                stats["errors"].append(str(exc))
    if dry_run:
        return {**stats, "would_write": len(rows)}
    ts = now_cn().isoformat()
    for item in rows:
        dao.upsert_instrument({**item, "created_at": ts, "updated_at": ts})
        dao.insert_universe_membership({
            "universe_id": DELISTED_UNIVERSE, "instrument_id": item["instrument_id"],
            "trade_date": item["delist_date"] or ts[:10],
            "status": "out", "reason": "delisted", "source": "akshare_delist",
        })
    return {**stats, "written": len(rows)}


def record_daily(dao: ResearchDAO | None = None, trade_date: str | None = None,
                 dry_run: bool = False) -> dict:
    """记录全市场当日状态到 universe_membership（coverage=current_only）。"""
    dao = dao or ResearchDAO()
    day = trade_date or now_cn().strftime("%Y-%m-%d")
    instruments = dao.list_instruments(limit=100000)
    if dry_run:
        return {"would_record": len(instruments), "trade_date": day}
    for inst in instruments:
        status = "in" if inst.get("status") == "active" else "out"
        reason = "delisted" if inst.get("status") == "delisted" else (
            "st" if inst.get("status") == "st" else "active")
        dao.insert_universe_membership({
            "universe_id": ALL_UNIVERSE, "instrument_id": inst["instrument_id"],
            "trade_date": day, "status": status, "reason": reason,
            "source": "daily_snapshot",
        })
    return {"recorded": len(instruments), "trade_date": day,
            "coverage": COVERAGE_CURRENT_ONLY}


def delisted_instruments(dao: ResearchDAO | None = None,
                         limit: int = 50) -> list[dict]:
    """退市股可查（按 delist_date 倒序）。"""
    dao = dao or ResearchDAO()
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM instrument WHERE status = 'delisted' "
            "ORDER BY delist_date DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
