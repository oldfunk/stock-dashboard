"""母项目接口探针（只读）：形状 / 取值 / 新鲜度，不建接口、不写母库。

为什么需要它：母项目是独立仓库，价值策略与行业/基准字段是跨仓契约。格式一变
（列改名、`ai_trade_strategy` 从 JSON 字符串换成新结构、指数代码换键），子项目
若只按“查不到就 unavailable”处理，会把**接口对歪**伪装成“母库没数据”。

探针只回答三件事：
1. **形状**：表/列/JSON 键是否还是约定形状；
2. **取值**：`signal` 是否仍是 BUY/HOLD/AVOID，`buy_zone` 是否可解析；
3. **新鲜度**：`run_date` / `snapshot_date` / `trade_date` 是否跟得上今天。

它不生成交易信号、不写任何库；输出可供 CLI、面板“数据来源”与日常核对使用。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Iterable, Optional

from .mother import _as_date, _columns, _conn, _stale
from .settings import mother_db_path

#: 价值策略（AI 交易建议）的权威位置
STRATEGY_TABLE = "screening_result"
STRATEGY_REQUIRED = ("code", "run_date", "ai_trade_strategy", "ai_failed",
                     "score", "status")
STRATEGY_SIGNALS = {"BUY", "HOLD", "AVOID"}
STRATEGY_CONFIDENCE = {"高", "中", "低"}
STRATEGY_MAX_AGE_DAYS = 3

#: 行业字段
SECTOR_TABLE = "stock_snapshot"
SECTOR_REQUIRED = ("code", "sector", "snapshot_date")
SECTOR_MAX_AGE_DAYS = 3

#: 指数日线（母项目 Issue #10 锁定的两套键）
BENCHMARK_TABLE = "benchmark_daily"
BENCHMARK_REQUIRED = ("code", "trade_date", "close")
BENCHMARK_CODES = ("sh000300", "sh000922", "000001", "399001", "399006")
BENCHMARK_MAX_AGE_DAYS = 3


def _probe_error(probe: str, code: str, detail: str, **extra) -> dict:
    d = {"probe": probe, "code": code, "detail": detail}
    d.update(extra)
    return d


def _base(name: str) -> dict:
    return {"name": name, "ok": False, "available": False,
            "source": f"mother:{name}", "asof": None, "stale": False,
            "errors": [], "warnings": [], "shape": {}}


def _open(mother, probe: str) -> tuple[Optional[sqlite3.Connection], dict]:
    """打开母库；不可用就把原因写进 errors，绝不抛给调用方。"""
    conn = _conn(mother)
    out = _base(probe)
    if conn is None:
        out["errors"].append(_probe_error(
            probe, "mother_db_unavailable",
            f"母库不可读：{mother_db_path(mother) or '未找到 stock_dashboard.db'}"))
        return None, out
    out["available"] = True
    return conn, out


def _parse_trade_payload(raw) -> tuple[Optional[dict], str, str]:
    """解析 `ai_trade_strategy`：返回 (dict|None, kind, error)。

    kind 记录实际形态：`dict` / `json_str` / `wrapped` / `empty` / `bad_json`。
    `wrapped` 兼容母库某些接口把行包在 `data` 里（2026-11-19 实测过一次）。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "empty", "ai_trade_strategy 为空"
    if isinstance(raw, dict):
        obj, kind = raw, "dict"
    else:
        try:
            obj = json.loads(str(raw))
            kind = "json_str"
        except (TypeError, ValueError):
            return None, "bad_json", "ai_trade_strategy 不是合法 JSON"
        if not isinstance(obj, dict):
            return None, "bad_shape", f"ai_trade_strategy 顶层是 {type(obj).__name__}"
    if "signal" not in obj and isinstance(obj.get("data"), dict):
        obj, kind = obj["data"], "wrapped"
    return obj, kind, ""


def probe_strategy(mother=None, today: Optional[str] = None,
                   limit: int = 60) -> dict:
    """探针：`screening_result.ai_trade_strategy` 的形状与取值。"""
    today = str(today or date.today().isoformat())[:10]
    conn, out = _open(mother, STRATEGY_TABLE)
    if conn is None:
        return out
    try:
        miss = [c for c in STRATEGY_REQUIRED
                if c not in _columns(conn, STRATEGY_TABLE)]
        if miss:
            out["errors"].append(_probe_error(
                STRATEGY_TABLE, "missing_columns", f"缺列：{miss}"))
            return out
        row = conn.execute(f"SELECT MAX(run_date) FROM {STRATEGY_TABLE}").fetchone()
        latest = _as_date(row[0] if row else None)
        out["asof"] = latest or None
        out["stale"] = _stale(latest, STRATEGY_MAX_AGE_DAYS, today) if latest else True
        rows = conn.execute(
            f"SELECT code, ai_trade_strategy, ai_failed, score FROM {STRATEGY_TABLE}"
            " WHERE run_date=? ORDER BY score DESC LIMIT ?", (latest, int(limit))
        ).fetchall() if latest else []
        if not rows:
            out["errors"].append(_probe_error(
                STRATEGY_TABLE, "no_rows", f"{latest or '无 run_date'} 没有策略行"))
            return out

        shape = {"rows": len(rows), "parsed": 0, "failed": 0,
                 "signals": {}, "confidence": {}, "kinds": {},
                 "missing_keys": {}, "empty_buy_zone": 0,
                 "missing_stop_loss": 0, "ai_failed_rows": 0}
        for r in rows:
            if int(r["ai_failed"] or 0):
                shape["ai_failed_rows"] += 1
                continue
            obj, kind, err = _parse_trade_payload(r["ai_trade_strategy"])
            shape["kinds"][kind] = shape["kinds"].get(kind, 0) + 1
            if err:
                shape["failed"] += 1
                out["errors"].append(_probe_error(
                    STRATEGY_TABLE, kind, f"{r['code']}：{err}"))
                continue
            shape["parsed"] += 1
            sig = str(obj.get("signal") or "").strip().upper()
            shape["signals"][sig or "<empty>"] = \
                shape["signals"].get(sig or "<empty>", 0) + 1
            if sig not in STRATEGY_SIGNALS:
                out["errors"].append(_probe_error(
                    STRATEGY_TABLE, "unknown_signal",
                    f"{r['code']}：signal={sig or '<empty>'}"))
            conf = str(obj.get("confidence") or "").strip()
            if conf:
                shape["confidence"][conf] = shape["confidence"].get(conf, 0) + 1
                if conf not in STRATEGY_CONFIDENCE:
                    out["warnings"].append(_probe_error(
                        STRATEGY_TABLE, "unknown_confidence",
                        f"{r['code']}：confidence={conf}"))
            zone = str(obj.get("buy_zone") or "").strip()
            if not zone:
                shape["empty_buy_zone"] += 1
                out["warnings"].append(_probe_error(
                    STRATEGY_TABLE, "empty_buy_zone", f"{r['code']}：buy_zone 为空"))
            for k in ("signal", "buy_zone", "stop_loss", "target_price"):
                if not str(obj.get(k) or "").strip():
                    shape["missing_keys"][k] = shape["missing_keys"].get(k, 0) + 1
            if not str(obj.get("stop_loss") or "").strip():
                shape["missing_stop_loss"] += 1

        out["shape"] = shape
        out["ok"] = not any(e["code"] != "no_rows" for e in out["errors"]) \
            and shape["parsed"] > 0
        return out
    except sqlite3.Error as exc:                 # noqa: BLE001
        out["errors"].append(_probe_error(
            STRATEGY_TABLE, "sqlite_error", f"{type(exc).__name__}: {exc}"))
        return out
    finally:
        conn.close()


def probe_sector(mother=None, today: Optional[str] = None) -> dict:
    """探针：`stock_snapshot(code, sector, snapshot_date)` 的覆盖与新鲜度。"""
    today = str(today or date.today().isoformat())[:10]
    conn, out = _open(mother, SECTOR_TABLE)
    if conn is None:
        return out
    try:
        miss = [c for c in SECTOR_REQUIRED
                if c not in _columns(conn, SECTOR_TABLE)]
        if miss:
            out["errors"].append(_probe_error(
                SECTOR_TABLE, "missing_columns", f"缺列：{miss}"))
            return out
        latest = _as_date(conn.execute(
            f"SELECT MAX(snapshot_date) FROM {SECTOR_TABLE}").fetchone()[0])
        out["asof"] = latest or None
        out["stale"] = _stale(latest, SECTOR_MAX_AGE_DAYS, today) if latest else True
        # `stock_snapshot` 是“每 code 一行、snapshot_date 记最后一次更新日”，
        # 不是按日全量快照。只统计 max(snapshot_date) 当天会把 831/5527 误读成
        # “覆盖率 15%”；契约要看的是**全表每个 code 的最新行业**。
        total, filled, fresh = conn.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN sector IS NOT NULL AND sector<>''"
            f" THEN 1 ELSE 0 END),"
            f" SUM(CASE WHEN snapshot_date=? THEN 1 ELSE 0 END)"
            f" FROM {SECTOR_TABLE}", (latest,)).fetchone() if latest else (0, 0, 0)
        total, filled, fresh = int(total or 0), int(filled or 0), int(fresh or 0)
        coverage = (filled / total) if total else 0.0
        out["shape"] = {"rows": total, "with_sector": filled,
                        "null_sector": total - filled,
                        "fresh_rows": fresh,
                        "coverage": round(coverage, 4)}
        if total == 0:
            out["errors"].append(_probe_error(
                SECTOR_TABLE, "no_rows", "stock_snapshot 没有行"))
            return out
        if coverage < 0.5:
            out["warnings"].append(_probe_error(
                SECTOR_TABLE, "low_coverage",
                f"sector 覆盖仅 {coverage:.1%}，可能源缺列或回填异常"))
        out["ok"] = coverage > 0
        return out
    except sqlite3.Error as exc:                 # noqa: BLE001
        out["errors"].append(_probe_error(
            SECTOR_TABLE, "sqlite_error", f"{type(exc).__name__}: {exc}"))
        return out
    finally:
        conn.close()


def probe_benchmark(mother=None, today: Optional[str] = None,
                    codes: Iterable[str] = BENCHMARK_CODES) -> dict:
    """探针：`benchmark_daily` 的键/新鲜度（Issue #10 锁定的两套键）。"""
    today = str(today or date.today().isoformat())[:10]
    conn, out = _open(mother, BENCHMARK_TABLE)
    if conn is None:
        return out
    try:
        miss = [c for c in BENCHMARK_REQUIRED
                if c not in _columns(conn, BENCHMARK_TABLE)]
        if miss:
            out["errors"].append(_probe_error(
                BENCHMARK_TABLE, "missing_columns", f"缺列：{miss}"))
            return out
        per: dict[str, dict] = {}
        latest_all = ""
        for code in codes:
            row = conn.execute(
                f"SELECT MAX(trade_date), COUNT(*) FROM {BENCHMARK_TABLE}"
                " WHERE code=?", (code,)).fetchone()
            d, n = _as_date(row[0] if row else None), int(row[1] or 0)
            per[code] = {"asof": d or None, "rows": n,
                         "stale": _stale(d, BENCHMARK_MAX_AGE_DAYS, today)
                         if d else True}
            latest_all = max(latest_all, d or "")
            if n == 0:
                out["errors"].append(_probe_error(
                    BENCHMARK_TABLE, "missing_code", f"{code} 没有数据"))
        out["asof"] = latest_all or None
        primary = per.get("sh000300") or {}
        out["stale"] = bool(primary.get("stale", True))
        out["shape"] = {"per_code": per,
                        "expected_codes": list(codes)}
        out["ok"] = not out["errors"]
        return out
    except sqlite3.Error as exc:                 # noqa: BLE001
        out["errors"].append(_probe_error(
            BENCHMARK_TABLE, "sqlite_error", f"{type(exc).__name__}: {exc}"))
        return out
    finally:
        conn.close()


def probe_all(mother=None, today: Optional[str] = None) -> dict:
    """一次跑完三组探针；`ok` 只代表形状可解析，新鲜度看各组的 `stale`。"""
    today = str(today or date.today().isoformat())[:10]
    parts = {
        "strategy": probe_strategy(mother, today),
        "sector": probe_sector(mother, today),
        "benchmark": probe_benchmark(mother, today),
    }
    return {
        "today": today,
        "mother_db": str(mother_db_path(mother) or ""),
        "ok": all(p["ok"] for p in parts.values()),
        "available": any(p["available"] for p in parts.values()),
        "stale": any(p["stale"] for p in parts.values()),
        "parts": parts,
    }
