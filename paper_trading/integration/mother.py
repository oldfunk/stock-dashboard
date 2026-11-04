"""母库只读富化：**每个接口独立降级**，不做整体切换。

设计要点：

1. **母库优先**：母库有该接口的数据就用母库（``source="mother:<table>"``）。
2. **逐接口回退**：母库没有该标的/该表不可用时，只回退该接口的本地来源；
   本地也没有则返回 ``source="unavailable"``（``data=None``），**不造假**。
3. **性能友好**：只读（``mode=ro`` + ``query_only``）、按 code 精确查询
   （母库已有索引）、内存 TTL 缓存、短超时。母库正在回填时不会被拖慢。
4. **来源标注**：所有返回值经 :func:`paper_trading.integration.sources.block`
   包装，带 ``source/asof/stale/available``。

注意（复权口径，T31 全表实测后如实）：母库 ``kline_daily`` 的
``open/high/low/close`` **四列都是前复权**（10,548,933 行里 ``close==close_qfq``
零例外，而与 ``close_raw`` 不等的占 80.6%）；**不复权的只有 ``close_raw`` 与
``volume/amount``** ⇒ 母库不能供 raw OHLC。子项目自有日线 ``daily_bars`` 也是前复权
（akshare ``qfq``），二者口径一致故绝对价位可比；引擎要的原始价走
``daily_bars_raw``（见 ``data/raw_feed.py``）。``daily_bars`` 返回 ``adj`` 标注实际口径。
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .settings import mother_db_path
from .sources import (SRC_LOCAL_AKSHARE, SRC_UNAVAILABLE, block, mother_source,
                      unavailable)

#: 默认缓存 TTL（秒）。日线盘中会变，取短；基本面/筛选取长。
TTL_QUOTES = 60.0
TTL_FUNDAMENTAL = 300.0
TTL_SCREENING = 600.0

_CACHE: dict = {}
_COLUMNS: dict = {}


def instrument_map(codes: Optional[Iterable[str]] = None,
                   mother: Optional[Path] = None,
                   ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库 `instrument` 表：symbol → instrument_id/market/exchange/name。

    母库研究 OS 以 instrument 为主键；子项目仍按 6 位代码，这里做映射。
    """
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    conn = _conn(mother)
    if conn is None or not _has(conn, "instrument", ("symbol",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 instrument 或不可读") for c in codes}
    try:
        def _fetch():
            if codes:
                q = ",".join("?" * len(codes))
                return [dict(r) for r in conn.execute(
                    f"""SELECT symbol, instrument_id, market_id, exchange, name, status
                        FROM instrument WHERE symbol IN ({q})""",
                    codes).fetchall()]
            return [dict(r) for r in conn.execute(
                """SELECT symbol, instrument_id, market_id, exchange, name, status
                   FROM instrument LIMIT 5000""").fetchall()]

        try:
            rows = _cached(f"instr:{_dbkey(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        out = {c: unavailable("母库未覆盖该标的 instrument") for c in codes}
        for r in rows:
            out[str(r["symbol"]).zfill(6)] = block(r, mother_source("instrument"))
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def trading_calendar(market: str = "CN", mother: Optional[Path] = None,
                     ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库 `trading_calendar`（市场交易日历）。空则 unavailable（调用方回退本地）。"""
    conn = _conn(mother)
    if conn is None or not _has(conn, "trading_calendar",
                                ("market_id", "trade_date", "is_open")):
        if conn is not None:
            conn.close()
        return unavailable("母库 trading_calendar 不可用")
    try:
        def _fetch():
            return [dict(r) for r in conn.execute(
                """SELECT market_id, trade_date, is_open, source
                   FROM trading_calendar WHERE market_id = ?
                   ORDER BY trade_date DESC LIMIT 2000""", (market,)).fetchall()]

        try:
            rows = _cached(f"tcal:{_dbkey(conn)}:{market}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        if not rows:
            return unavailable("母库 trading_calendar 无数据（回填中）")
        asof = max(_as_date(r.get("trade_date")) for r in rows if r.get("trade_date"))
        return block(rows, mother_source("trading_calendar"), asof=asof,
                     max_age_days=7)
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def coverage(codes: Optional[Iterable[str]] = None,
             local_bars: Optional[Callable[[str, int], list]] = None,
             mother: Optional[Path] = None) -> dict:
    """每个富化接口的覆盖情况汇总（供 CLI `data coverage` / 面板排查）。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    out: dict = {}
    if codes:
        out["daily_bars"] = daily_bars(codes, local_bars=local_bars, mother=mother)
        out["financial_summary"] = financial_summary(codes, mother=mother)
        out["dividend"] = dividend(codes, mother=mother)
        out["thesis"] = thesis(codes, mother=mother)
        out["outcomes"] = outcomes(codes, mother=mother)
        out["instrument"] = instrument_map(codes, mother=mother)
    out["screening"] = screening_detail(mother=mother)
    out["regime"] = regime(mother=mother)
    out["news"] = news(codes=codes[:10], mother=mother)
    out["benchmark"] = benchmark(["000001", "399001", "399006"], mother=mother)
    out["trading_calendar"] = trading_calendar(mother=mother)
    return out


# ---------- 本地 vs 母库行情对照（切库验证） ----------

def _ma(vals: list, n: int) -> list:
    out = []
    for i in range(len(vals)):
        out.append(None if i + 1 < n else sum(vals[i + 1 - n:i + 1]) / n)
    return out


def _crosses(ma5: list, ma20: list) -> tuple:
    """统计金叉/死叉次数（基于 MA5-MA20 符号变化）。"""
    golden = death = 0
    prev = 0
    for a, b in zip(ma5, ma20):
        if a is None or b is None:
            continue
        s = 1 if a > b else (-1 if a < b else 0)
        if prev != 0 and s != 0 and s != prev:
            if s > 0:
                golden += 1
            else:
                death += 1
        if s != 0:
            prev = s
    return golden, death


def _corr(a: list, b: list):
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b))
                 / (va ** 0.5 * vb ** 0.5), 4)


def compare_bars(codes: Iterable[str], days: int = 250,
                 local_bars: Optional[Callable[[str, int], list]] = None,
                 mother: Optional[Path] = None) -> dict:
    """对照本地前复权与母库日线：MA 交叉差异 + 收益相关性。

    用途：验证“切到母库行情”对策略信号的影响。母库交付 `close_qfq`
    （母方 Issue #8，2026-10-27 验收）后与本地 akshare qfq 同口径，绝对
    价位可比；老数据（`close_qfq` 为 NULL）降级后复权/不复权，仍按均线
    交叉与收益相关性对照。
    """
    codes = [str(c).strip().zfill(6) for c in codes]
    mother_bars = daily_bars(codes, days=days, local_bars=None, mother=mother)
    local_b = daily_bars(codes, days=days, local_bars=local_bars,
                         mother=mother, force_local=True)
    out: dict = {}
    for c in codes:
        def _series(blk):
            rows = (blk or {}).get("bars") or []
            return ([r["close"] for r in rows if r.get("close")],
                    (blk or {}).get("adj"),
                    [r.get("date") for r in rows])

        mcloses, madj, mdates = _series(mother_bars.get(c))
        lcloses, ladj, ldates = _series(local_b.get(c))
        mg, md = _crosses(_ma(mcloses, 5), _ma(mcloses, 20))
        lg, ld = _crosses(_ma(lcloses, 5), _ma(lcloses, 20))
        corr = None
        m_map = {d: v for d, v in zip(mdates, mcloses)}
        l_map = {d: v for d, v in zip(ldates, lcloses)}
        common = sorted(set(m_map) & set(l_map))
        if len(common) >= 10:
            mret = [m_map[common[i + 1]] / m_map[common[i]] - 1
                    for i in range(len(common) - 1)]
            lret = [l_map[common[i + 1]] / l_map[common[i]] - 1
                    for i in range(len(common) - 1)]
            corr = _corr(mret, lret)
        out[c] = {
            "local": {"adj": ladj, "n": len(lcloses),
                      "last": ldates[0] if ldates else None,
                      "golden": lg, "death": ld},
            "mother": {"adj": madj, "n": len(mcloses),
                       "last": mdates[0] if mdates else None,
                       "golden": mg, "death": md},
            "cross_diff": {"golden": mg - lg, "death": md - ld},
            "return_corr": corr,
        }
    return out


def reset_cache() -> None:
    """清空内存缓存（测试/运维用）。"""
    _CACHE.clear()
    _COLUMNS.clear()
    _scale_warned.clear()


def _conn(mother: Optional[Path] = None):
    """只读连接母库；不可用返回 None。"""
    db = mother_db_path(mother)
    if db is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=2000")
        # 列缓存键含库路径（_columns 内），不同库天然区分——不再每次清
        # （旧实现每次 _conn 都 clear，列缓存同样永不命中，R4.2 一并修）
        return conn
    except Exception:                       # noqa: BLE001 - 母库不可用即降级
        return None


def _columns(conn, table: str) -> set:
    """表的列名集合（带缓存）；表不存在返回空集。

    键含库路径：同一库跨连接复用，不同库（测试多母库、真库切换）互不串。
    """
    dkey = _dbkey(conn)
    key = (dkey, table)
    if key in _COLUMNS:
        return _COLUMNS[key]
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {str(r[1]) for r in rows}
    except Exception:                       # noqa: BLE001
        cols = set()
    _COLUMNS[key] = cols
    return cols


def _has(conn, table: str, required: Iterable[str]) -> bool:
    cols = _columns(conn, table)
    return bool(cols) and all(c in cols for c in required)


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _dbkey(conn) -> str:
    """库路径做缓存键（稳定跨连接）。

    旧实现用 ``_dbkey(conn)``（每连接单调序号）——而 ``_conn`` 每次调用都
    新建连接，序号永远不同，TTL 缓存跨调用**永不命中**（2026-10-27
    R4.2 审查发现，文档声称的 60s/300s 缓存形同虚设）。库路径同一库
    跨调用稳定、不同库（测试多母库）天然区分；schema 变更由 TTL 兜底
    （最长 60-300s 旧口径窗口，可接受且与文档 TTL 语义一致）。
    查不到路径时回退 id(conn)（缓存退化为不跨调用，只求不串库）。
    """
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row and row[2]:
            return str(row[2])
    except Exception:                       # noqa: BLE001
        pass
    return f"conn:{id(conn)}"


def _as_date(value) -> str:
    s = str(value or "")[:10]
    return s


def _stale(asof: Optional[str], max_age_days: int, today: str) -> bool:
    if not asof:
        return False
    from datetime import date
    try:
        d = date.fromisoformat(asof)
        t = date.fromisoformat(today)
        return (t - d).days > max_age_days
    except ValueError:
        return False


# ---------- 日线：母库 kline_daily → 本地 akshare ----------

#: 同日双源价差容差。实测同日 qfq vs qfq 差 <0.01%（data compare corr≈1.0）；
#: 取错列/复权口径污染是 1.2×~8× 量级，10% 能全拦且几乎零误报。
SCALE_TOLERANCE = 0.10
#: 绝对量级上限（H-7）：qfq 契约下 A 股真实股价 <1 万元/股；
#: 10 万留足余量，只拦“分为单位/小数点错位”级错误
ABS_PRICE_SANITY_MAX = 100000.0
_LOGGER = logging.getLogger(__name__)
_scale_warned: set = set()


def _norm_local(local: Iterable) -> list:
    """本地 Bar/字典 → daily_bars 输出结构。"""
    norm = []
    for b in local or []:
        if isinstance(b, dict):
            ts = str(b.get("timestamp") or b.get("date") or "")
            norm.append({
                "date": _as_date(ts),
                "open": b.get("open"), "high": b.get("high"),
                "low": b.get("low"), "close": b.get("close"),
                "volume": b.get("volume"),
                "amount": b.get("amount"),
            })
        else:
            ts = str(getattr(b, "timestamp", "") or "")
            norm.append({
                "date": _as_date(ts),
                "open": getattr(b, "open", None),
                "high": getattr(b, "high", None),
                "low": getattr(b, "low", None),
                "close": getattr(b, "close", None),
                "volume": getattr(b, "volume", None),
                "amount": None,
            })
    return norm


def _scale_guard(mother_bars: list, local_bars: list) -> Optional[dict]:
    """跨源同日尺度对照（2026-10-27 事故闸）。

    找两边**共有的最近日期**比收盘价：同日 qfq vs qfq 应几乎相等；
    比值超 ``SCALE_TOLERANCE`` = 口径/取错列类尺度污染 → 拒用母库这批 bars。
    无共有日期返回 None（无法对照则放行——本地兜底仍在，且不误杀母库
    独有标的：本地没有该票时无法对照，只能信 provenance 标注）。
    """
    if not mother_bars or not local_bars:
        return None
    local_by = {b["date"]: b["close"] for b in local_bars
                if b.get("date") and b.get("close")}
    for mb in mother_bars:            # 母库返回倒序，首个命中即最近共有日
        d = mb.get("date")
        lc = local_by.get(d)
        if lc is None or not mb.get("close"):
            continue
        try:
            ratio = float(mb["close"]) / float(lc)
        except (TypeError, ValueError, ZeroDivisionError):
            return {"ok": False, "date": d, "reason": "bad-value"}
        return {"ok": abs(ratio - 1.0) <= SCALE_TOLERANCE,
                "date": d,
                "mother_close": round(float(mb["close"]), 4),
                "local_close": round(float(lc), 4),
                "ratio": round(ratio, 4),
                "tolerance": SCALE_TOLERANCE}
    return None


def daily_bars(codes: Iterable[str], days: int = 250,
               local_bars: Optional[Callable[[str, int], list]] = None,
               mother: Optional[Path] = None,
               ttl: float = TTL_QUOTES,
               force_local: bool = False,
               today: Optional[str] = None) -> dict:
    """每只标的的日线（母库优先，缺该标的回退本地）。

    ``local_bars(code, days)`` 由调用方注入（返回本地 Bar 列表或字典列表）。
    ``force_local=True`` 时只看本地（用于本地 vs 母库对照）。
    ``today``（T53）：新鲜度判定的基准日；缺省取墙钟。**测试请显式传**——
    写死日期 + 墙钟判定就是这仓两次日期 flaky 的成因，注入后断言与日历无关。
    返回 ``{code: {"available","source","asof","adj","stale","bars":[...]}}``。
    """
    out: dict = {}
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    conn = None if force_local else _conn(mother)
    try:
        for code in dict.fromkeys(codes):
            bars: list = []
            source = SRC_UNAVAILABLE
            asof = None
            adj = None
            if conn is not None and _has(conn, "kline_daily",
                                         ("code", "trade_date", "close")):
                # 复权口径优先级（T31 实测后如实）：close_qfq（前复权，母方 Issue #8
                # 交付，与本地 akshare qfq 同口径、绝对价位可比）→ close_adj（后复权，
                # 绝对价位不可比）→ **close**。注意最后一级**不是不复权**：母库的
                # ``close`` 列实测与 ``close_qfq`` 全表一致（10,548,933 行零例外），
                # 所以整条链实际是 qfq → hfq → qfq；真要不复权请读 ``close_raw``。
                # close_qfq 列不存在或为 NULL（回填中）时逐行降级，不影响其他行。
                has_qfq = _has(conn, "kline_daily", ("close_qfq",))
                cols = ("trade_date, open, high, low, close, volume, amount, "
                        "close_adj" + (", close_qfq" if has_qfq else ""))

                def _fetch():
                    rows = conn.execute(
                        f"""SELECT {cols}
                            FROM kline_daily WHERE code = ?
                            ORDER BY trade_date DESC LIMIT ?""",
                        (code, int(days))).fetchall()
                    return [dict(r) for r in rows]

                try:
                    rows = _cached(f"kline:{_dbkey(conn)}:{code}:{days}", ttl, _fetch)
                except Exception:           # noqa: BLE001 - 单只失败不影响其他
                    rows = []
                if rows:
                    def _close(r):
                        return (r.get("close_qfq") or r.get("close_adj")
                                or r["close"])

                    bars = [{
                        "date": _as_date(r["trade_date"]),
                        "open": r["open"], "high": r["high"],
                        "low": r["low"], "close": _close(r),
                        "volume": r["volume"],
                        "amount": r.get("amount"),
                    } for r in rows]
                    source = mother_source("kline_daily")
                    asof = _as_date(rows[0]["trade_date"])
                    r0 = rows[0]
                    # 标签必须与**实际取到的那一列**一致（§5.5 裁决 1）：兜底列
                    # ``close`` 是前复权，不是不复权 ⇒ 这里不能再报 "raw"，
                    # 否则 provenance 会把 qfq 数字当成交易所价来用（尺度污染事故类）。
                    adj = ("hfq" if not r0.get("close_qfq") and r0.get("close_adj")
                           else "qfq")
            # —— 尺度闸（v0.7.6，跨源对照，2026-10-27 事故教训）——
            # 母库有价且调用方给了本地源时，同日收盘比对；超容差=尺度污染
            # （取错列/复权口径回退），拒用母库这批 bars 回退本地并告警。
            # 注意：唯一的独立基准是本地 qfq；同源自洽的校验挡不住尺度错误。
            check: Optional[dict] = None
            if bars and local_bars is not None:
                try:
                    check = _scale_guard(
                        bars, _norm_local(local_bars(code, days) or []))
                except Exception:           # noqa: BLE001 - 闸自身故障不挡数据
                    check = None
                if check is not None and not check.get("ok"):
                    if code not in _scale_warned:
                        _scale_warned.add(code)
                        _LOGGER.warning(
                            "母库日线尺度异常，已回退本地：%s %s", code, check)
                    bars, source, asof, adj = [], SRC_UNAVAILABLE, None, None
            # 绝对量级 sanity（H-7，2026-10-27 同事审查）：无跨源对照时的
            # 最后防线——qfq 契约下 close ≤0 或 >10 万元/股 = 单位级错误
            # （如以“分”计价 ×10000），拒用回退本地。相对偏差（7.9× 这类）
            # 由 R1.3 跨源闸负责，此闸只拦绝对荒谬值。
            if bars:
                try:
                    _c0 = float(bars[0].get("close") or 0.0)
                except (TypeError, ValueError):
                    _c0 = 0.0
                if not (0.0 < _c0 <= ABS_PRICE_SANITY_MAX):
                    if code not in _scale_warned:
                        _scale_warned.add(code)
                        _LOGGER.warning("母库日线量级非法（close=%r），"
                                        "回退本地：%s", _c0, code)
                    bars, source, asof, adj = [], SRC_UNAVAILABLE, None, None
            if not bars and local_bars is not None:
                try:
                    local = local_bars(code, days) or []
                except Exception:           # noqa: BLE001
                    local = []
                if local:
                    norm = _norm_local(local)
                    # H-6（同事审查）：本地返回顺序由调用方决定，必须自己
                    # 排序并以最新日为 asof——否则 stale/新鲜度判定失真
                    norm.sort(key=lambda r: r["date"] or "")
                    bars = norm
                    source = SRC_LOCAL_AKSHARE
                    asof = bars[-1]["date"] if bars else None
                    adj = "qfq"
            out[code] = {
                "available": bool(bars), "source": source, "asof": asof,
                "adj": adj,
                "stale": bool(_stale(asof, 3, today or date.today().isoformat()))
                         if asof else False,
                "bars": bars,
                "scale_check": check,   # 同日双源对照（None=无法对照/未启用）
            }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:               # noqa: BLE001
                pass
    return out


def raw_close_map(codes: Iterable[str], days: int = 30,
                  mother: Optional[Path] = None,
                  ttl: float = TTL_QUOTES,
                  since: Optional[str] = None) -> dict:
    """母库**不复权**列：`{code: {date: {"close": close_raw, "volume":, "amount":}}}`。

    T31 用它做 raw 管道的**独立同日对照**（谁写引擎谁不能被引擎的数据自证）。
    实测（NEC 生产母库 2026-10-28）：母库只有 `close_raw/volume/amount` 是不复权，
    `open/high/low/close` 四列都是**前复权**（600519 2016-01-04：
    open 172.87 / close 166.54 = close_qfq，而 close_raw=210.02 = 交易所价）
    ⇒ 母库不能供 raw OHLC，只能供 raw 收盘与量额；raw OHLC 由 akshare
    `stock_zh_a_daily(adjust="")` 供（同日实测与 close_raw/volume/amount 完全一致）。

    `since`（T41）：按**日期下界**取参照，而不是只取最近 `days` 行。
    参照窗口必须覆盖采集窗口，否则老的那一半 bar 天生没有参照——
    `days` 是 `LIMIT`（交易日行数），采集是 `days*2+10` **自然日**，
    两者单位不同，实测 NEC 590 行只验了 400 行（report-t39 §6-B）。
    """
    out: dict = {}
    conn = _conn(mother)
    if conn is None:
        return out
    try:
        if not _has(conn, "kline_daily", ("code", "trade_date", "close_raw")):
            return out
        for code in dict.fromkeys(str(c).strip().zfill(6) for c in (codes or [])):
            def _fetch():
                if since:
                    rows = conn.execute(
                        """SELECT trade_date, close_raw, volume, amount
                           FROM kline_daily WHERE code = ? AND trade_date >= ?
                           ORDER BY trade_date""", (code, str(since)[:10])).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT trade_date, close_raw, volume, amount
                           FROM kline_daily WHERE code = ?
                           ORDER BY trade_date DESC LIMIT ?""",
                        (code, int(days))).fetchall()
                return {str(dict(r)["trade_date"])[:10]: dict(r) for r in rows}
            try:
                key = f"rawclose:{_dbkey(conn)}:{code}:{days}:{since or '-'}"
                out[code] = _cached(key, ttl, _fetch)
            except Exception:               # noqa: BLE001 - 单只失败不影响其他
                out[code] = {}
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass
    return out


# ---------- 基本面 / 财务 / 股息 / 基准 / 新闻 / 筛选 / 论点 / 追踪 ----------

def raw_close_asof(codes: Iterable[str], mother: Optional[Path] = None,
                   ttl: float = TTL_QUOTES) -> dict:
    """验料源自身的新鲜度：`{code: close_raw 已覆盖到的最后一天}`。

    G-T13-1 的"当日允许 checked=0，T+1 必须翻转"要靠它区分
    **母库还没给参照**（`waiting_reference`，只告警）与
    **参照已有我们却没重跑对照**（`checked_not_flipped`，停线）。
    """
    out: dict = {}
    conn = _conn(mother)
    if conn is None:
        return out
    try:
        if not _has(conn, "kline_daily", ("code", "trade_date", "close_raw")):
            return out
        for code in dict.fromkeys(str(c).strip().zfill(6) for c in (codes or [])):
            def _fetch():
                r = conn.execute(
                    """SELECT MAX(trade_date) FROM kline_daily
                       WHERE code = ? AND close_raw IS NOT NULL""", (code,)).fetchone()
                return _as_date(r[0]) if r and r[0] else ""
            try:
                out[code] = _cached(f"rawasof:{_dbkey(conn)}:{code}", ttl, _fetch)
            except Exception:               # noqa: BLE001
                out[code] = ""
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass
    return out


def financial_summary(codes: Iterable[str], mother: Optional[Path] = None,
                      ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库财务摘要（ROE 5y/10y、毛利/净利率、OCF、负债、ROIC）。无本地等价 → unavailable。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "financial_summary", ("stock_code",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 financial_summary 或不可读") for c in codes}
    try:
        # 显式列清单（docs/mother-interface.md §6 承诺不 SELECT *）：
        # 列名从 PRAGMA 动态取，母库加列自动跟随，不写死第二套 schema。
        all_cols = sorted(_columns(conn, "financial_summary") - {"stock_code"})
        if not all_cols:      # 表只有 stock_code：退化为常量列，避免 SQL 语法错
            all_cols = ["stock_code"]
        col_sql = ", ".join(f'"{c}"' for c in all_cols)

        def _fetch():
            q = (",".join("?" * len(codes)))
            if col_sql == "stock_code":
                select_sql = "stock_code"
            else:
                select_sql = f"stock_code, {col_sql}"
            rows = conn.execute(
                f"SELECT {select_sql} FROM financial_summary "
                f"WHERE stock_code IN ({q})",
                codes).fetchall()
            return [dict(r) for r in rows]

        try:
            rows = _cached(f"fin:{_dbkey(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        out = {c: unavailable("母库未覆盖该标的财务摘要") for c in codes}
        for r in rows:
            c = str(r["stock_code"]).zfill(6)
            out[c] = block(r, mother_source("financial_summary"),
                           asof=_as_date(r.get("updated_at")), max_age_days=30)
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def dividend(codes: Iterable[str], mother: Optional[Path] = None,
             ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库年度分红。无本地等价 → unavailable。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "dividend_annual", ("stock_code",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 dividend_annual 或不可读") for c in codes}
    try:
        q = ",".join("?" * len(codes))

        def _fetch():
            return [dict(r) for r in conn.execute(
                f"""SELECT stock_code, report_date, cash_per_share, eps,
                           reported_yield, source
                    FROM dividend_annual WHERE stock_code IN ({q})
                    ORDER BY stock_code, report_date DESC""", codes).fetchall()]

        try:
            rows = _cached(f"div:{_dbkey(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(str(r["stock_code"]).zfill(6), []).append(r)
        out = {c: unavailable("母库未覆盖该标的股息") for c in codes}
        for c, items in grouped.items():
            out[c] = block(items, mother_source("dividend_annual"),
                           asof=_as_date(items[0]["report_date"]),
                           max_age_days=400)
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def benchmark(codes: Iterable[str], days: int = 60,
              mother: Optional[Path] = None,
              ttl: float = TTL_QUOTES) -> dict:
    """母库基准指数日线（相对强度用）。无本地等价 → unavailable。"""
    codes = [str(c).strip() for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "benchmark_daily", ("code", "trade_date", "close")):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 benchmark_daily 或不可读") for c in codes}
    try:
        q = ",".join("?" * len(codes))

        def _fetch():
            return [dict(r) for r in conn.execute(
                f"""SELECT code, trade_date, close FROM benchmark_daily
                    WHERE code IN ({q}) ORDER BY code, trade_date DESC""",
                codes).fetchall()]

        try:
            rows = _cached(f"bench:{_dbkey(conn)}:{','.join(codes)}:{days}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(str(r["code"]), []).append(r)
        out = {c: unavailable("母库未覆盖该基准") for c in codes}
        for c, items in grouped.items():
            items = items[:int(days)]
            out[c] = block([{"date": _as_date(x["trade_date"]), "close": x["close"]}
                            for x in items], mother_source("benchmark_daily"),
                           asof=_as_date(items[0]["trade_date"]), max_age_days=3)
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def news(codes: Optional[Iterable[str]] = None, limit: int = 20,
         local_news: Optional[Callable[[], dict]] = None,
         mother: Optional[Path] = None,
         ttl: float = TTL_FUNDAMENTAL) -> dict:
    """新闻：母库 ``news_item`` 优先（个股 + 全局），缺则回退本地 akshare 快讯。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    conn = _conn(mother)
    if conn is not None and _has(conn, "news_item", ("code", "title")):
        try:
            def _fetch():
                rows = []
                if codes:
                    q = ",".join("?" * len(codes))
                    rows += [dict(r) for r in conn.execute(
                        f"""SELECT code, name, title, summary, source, published_at,
                                   sentiment, ai_brief
                            FROM news_item WHERE code IN ({q})
                            ORDER BY published_at DESC LIMIT ?""",
                        codes + [int(limit)]).fetchall()]
                rows += [dict(r) for r in conn.execute(
                    """SELECT code, name, title, summary, source, published_at,
                              sentiment, ai_brief
                       FROM news_item WHERE code = '' ORDER BY published_at DESC LIMIT ?""",
                    (int(limit),)).fetchall()]
                return rows

            try:
                rows = _cached(f"news:{_dbkey(conn)}:{','.join(codes)}:{limit}", ttl, _fetch)
            except Exception:               # noqa: BLE001
                rows = []
            if rows:
                asof = _as_date(max(str(r.get("published_at") or "") for r in rows))
                return block(rows, mother_source("news_item"), asof=asof,
                             max_age_days=7)
        finally:
            try:
                conn.close()
            except Exception:               # noqa: BLE001
                pass
    # 回退本地 akshare 快讯（调用方注入，避免 integration 依赖 data.news）
    if local_news is not None:
        try:
            n = local_news() or {}
        except Exception:                   # noqa: BLE001
            n = {}
        if n.get("available"):
            return block(n.get("items"), SRC_LOCAL_AKSHARE,
                         asof=n.get("fetched_at") or None)
    return unavailable("母库与本地均无新闻")


def screening_detail(local_pool: Optional[list] = None,
                     mother: Optional[Path] = None,
                     ttl: float = TTL_SCREENING) -> dict:
    """母库最新一轮筛选明细（含 score_detail/reason/strategy_tags）。

    母库不可用/无结果时回退本地手填池（无评分，仅代码列表）。
    """
    conn = _conn(mother)
    if conn is not None and _has(conn, "screening_result", ("run_id", "run_date")):
        try:
            def _fetch():
                run = conn.execute(
                    "SELECT run_id, run_date FROM screening_result "
                    "GROUP BY run_id ORDER BY run_date DESC, run_id DESC LIMIT 1"
                ).fetchone()
                if not run:
                    return None
                rows = [dict(r) for r in conn.execute(
                    """SELECT code, name, score, pe, pb, roe, reason, strategy_tags,
                              dividend_yield, score_detail
                       FROM screening_result WHERE run_id = ?
                       ORDER BY score DESC""", (run["run_id"],)).fetchall()]
                return {"run_id": run["run_id"], "run_date": _as_date(run["run_date"]),
                        "rows": rows}

            try:
                run = _cached(f"screening:latest:{_dbkey(conn)}", ttl, _fetch)
            except Exception:               # noqa: BLE001
                run = None
            if run and run.get("rows"):
                return block(run, mother_source("screening_result"),
                             asof=run.get("run_date"), max_age_days=7)
        finally:
            try:
                conn.close()
            except Exception:               # noqa: BLE001
                pass
    if local_pool:
        return block({"run_id": None, "run_date": None,
                      "rows": [{"code": str(c).zfill(6)} for c in local_pool]},
                     "local:config", asof=None)
    return unavailable("母库无筛选结果且本地无手填池")


def thesis(codes: Iterable[str], mother: Optional[Path] = None,
           ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库观察池论点（core_thesis / sell_conditions）。无本地等价 → unavailable。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "watchlist_thesis", ("code",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 watchlist_thesis 或不可读") for c in codes}
    try:
        q = ",".join("?" * len(codes))

        def _fetch():
            return [dict(r) for r in conn.execute(
                f"""SELECT code, core_thesis, assumptions, red_lines, sell_conditions
                    FROM watchlist_thesis WHERE code IN ({q})""", codes).fetchall()]

        try:
            rows = _cached(f"thesis:{_dbkey(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        out = {c: unavailable("母库未覆盖该标的论点") for c in codes}
        for r in rows:
            c = str(r["code"]).zfill(6)
            out[c] = block(r, mother_source("watchlist_thesis"))
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def outcomes(codes: Iterable[str], mother: Optional[Path] = None,
             ttl: float = TTL_FUNDAMENTAL) -> dict:
    """母库 AI 结论追踪（analysis_outcome）。覆盖度低时明确提示，不阻断。"""
    codes = [str(c).strip().zfill(6) for c in (codes or [])]
    if not codes:
        return {}
    conn = _conn(mother)
    if conn is None or not _has(conn, "analysis_outcome", ("stock_code",)):
        if conn is not None:
            conn.close()
        return {c: unavailable("母库无 analysis_outcome 或不可读") for c in codes}
    try:
        q = ",".join("?" * len(codes))

        def _fetch():
            return [dict(r) for r in conn.execute(
                f"""SELECT stock_code, analysis_date, signal, confidence, verdict,
                           entry_price, ret_20, ret_60, excess_20, excess_60
                    FROM analysis_outcome WHERE stock_code IN ({q})
                    ORDER BY analysis_date DESC""", codes).fetchall()]

        try:
            rows = _cached(f"outcome:{_dbkey(conn)}:{','.join(codes)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(str(r["stock_code"]).zfill(6), []).append(r)
        out = {}
        for c in codes:
            items = grouped.get(c) or []
            if not items:
                out[c] = unavailable("母库未覆盖该标的的 AI 追踪")
                continue
            judged = [x for x in items if str(x.get("verdict") or "").strip()]
            out[c] = block(items, mother_source("analysis_outcome"),
                           asof=_as_date(items[0]["analysis_date"]),
                           note=f"{len(judged)}/{len(items)} 条有 verdict"
                                if judged else "verdict 未落全（母库回填中）",
                           max_age_days=180)
        return out
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass


def regime(mother: Optional[Path] = None,
           ttl: float = TTL_QUOTES) -> dict:
    """母库大盘状态（指数最新值/涨跌幅/PE/PB）。无本地等价 → unavailable。"""
    conn = _conn(mother)
    if conn is None or not _has(conn, "market_index", ("index_code", "date")):
        if conn is not None:
            conn.close()
        return unavailable("母库无 market_index 或不可读")
    try:
        def _fetch():
            rows = []
            for code in ("000001", "399001", "399006"):
                r = conn.execute(
                    """SELECT index_code, index_name, current_value, change_percent,
                              pe, pb, date FROM market_index WHERE index_code = ?
                       ORDER BY date DESC LIMIT 1""", (code,)).fetchone()
                if r:
                    rows.append(dict(r))
            return rows

        try:
            rows = _cached(f"regime:{_dbkey(conn)}", ttl, _fetch)
        except Exception:                   # noqa: BLE001
            rows = []
        if not rows:
            return unavailable("母库大盘数据为空")
        asof = max(_as_date(r.get("date")) for r in rows if r.get("date"))
        return block(rows, mother_source("market_index"), asof=asof,
                     max_age_days=7)
    finally:
        try:
            conn.close()
        except Exception:                   # noqa: BLE001
            pass
