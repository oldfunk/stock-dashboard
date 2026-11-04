"""行情数据源：母库优先 / 本地前复权（逐标的降级，带来源标注）。

背景：母库 `kline_daily` 覆盖更全更新，但复权口径不同（后复权/不复权）；
本地 `data.db.daily_bars` 是 akshare 前复权。执行侧（引擎/AI 信号/计价）
默认 **母库优先、缺该标的回退本地**（`data.daily_source=auto`），
可用 `local` 强制只用本地、`mother` 强制只用母库。

本模块只做“读哪份”，不改变复权口径；口径差异由 `integration.mother.compare_bars`
量化并记入文档/Issue。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from paper_trading.models import Bar


def _to_bar(symbol: str, row: dict) -> Bar:
    ts = str(row.get("date") or row.get("timestamp") or "")[:10]
    try:
        when = datetime.fromisoformat(ts) if ts else datetime.now()
    except ValueError:
        when = datetime.now()
    return Bar(symbol=symbol, timestamp=when,
               open=float(row.get("open") or 0.0),
               high=float(row.get("high") or 0.0),
               low=float(row.get("low") or 0.0),
               close=float(row.get("close") or 0.0),
               volume=int(row.get("volume") or 0))


class BarSource:
    """按标的选择行情来源（母库优先/本地兜底）。"""

    def __init__(self, data_db, source: str = "auto",
                 mother: Optional[Path] = None) -> None:
        self.db = data_db
        self.source = (source or "auto").lower()
        self.mother = mother
        #: 引擎取数被拒的原因（T31 fail-closed 的可审计出口）
        self.last_engine_reject: Optional[str] = None

    def get_bars(self, symbol: str, limit: int = 30) -> list:
        """最近 limit 根日线（时间升序，与本地 get_bars 同序）。

        `mother` 模式（M-1 修复）：只用母库、**不回退本地**——母库缺该票
        即返回 []（fail-closed，缺数据不下单），与 config 文档语义一致；
        `auto` 才是母库优先+本地兜底。
        """
        if self.source == "local":
            return self.db.get_bars(symbol, limit=limit)
        try:
            from paper_trading.integration import mother as _m

            # mother-only 模式不传 local_bars：不做本地回退（严格口径）；
            # 绝对量级 sanity（H-7）在 daily_bars 内仍然生效。
            blk = _m.daily_bars(
                [symbol], days=limit,
                local_bars=(lambda c, d: self.db.get_bars(c, limit=d))
                if self.source != "mother" else None,
                mother=self.mother)
            b = blk.get(symbol) or {}
            if b.get("available") and b.get("bars"):
                bars = [_to_bar(symbol, x) for x in b["bars"]]
                bars.sort(key=lambda x: x.timestamp)   # 母库返回倒序，统一升序
                return bars[-int(limit):]
        except Exception:                           # noqa: BLE001 - 回退本地
            pass
        if self.source == "mother":
            return []           # M-1：mother 模式缺母库 = 无数据，不回退
        return self.db.get_bars(symbol, limit=limit)

    def engine_raw_bars(self, symbol: str, limit: int = 30,
                        today=None, max_lag: int = 1,
                        require_today_reference: bool = True) -> list:
        """引擎唯一允许的取数口：**只有原始价，且必须新鲜**（T31 G-T13-1/2）。

        任何不满足的情况返回 `[]`——按 AGENTS"缺数据不下单"处理，
        绝不降级回 qfq 列（那正是 2026-10-27 尺度污染的路径）。
        拒绝原因写在 `self.last_engine_reject`，由调用方入 op_log。

        `require_today_reference`（T50-C，默认开）：**今天这根没有独立参照就不喂**——
        盘后采集若没做成当日跨源对照（母库 NULL + 收盘快照也没对上），
        `checked=0` 的当日 bar 属于"未经验证的价"，不许进撮合。
        """
        self.last_engine_reject = None
        try:
            bars = self.db.get_raw_bars(symbol, limit=limit)
        except sqlite3.OperationalError as e:      # 表不存在=管道没建好
            self.last_engine_reject = f"raw_table_missing: {e}"
            return []
        if not bars:
            self.last_engine_reject = "no_raw_data"
            return []
        bad = [b for b in bars if (b.adjust or "raw") != "raw"]
        if bad:
            self.last_engine_reject = f"caliber_not_raw: {bad[0].adjust!r}"
            return []
        from paper_trading.data.raw_feed import raw_freshness, today_reference_state

        fresh = raw_freshness(self.db, [symbol], today=today, max_lag=max_lag).get(symbol, {})
        if fresh.get("stale"):
            self.last_engine_reject = (f"raw_stale: last={fresh.get('last')} "
                                       f"lag={fresh.get('lag')}")
            return []
        if require_today_reference:
            st = today_reference_state(self.db, symbol, today=today)
            if st["state"] == "unchecked":
                self.last_engine_reject = f"today_bar_unchecked: {st['date']}"
                return []
        return bars

    def source_of(self, symbol: str) -> str:
        """该标的当前实际用的来源（用于审计/日志）。"""
        if self.source == "local":
            return "local:akshare"
        try:
            from paper_trading.integration import mother as _m
            from paper_trading.integration.sources import SRC_UNAVAILABLE

            blk = _m.daily_bars(
                [symbol], days=30,
                local_bars=(lambda c, d: self.db.get_bars(c, limit=d))
                if self.source != "mother" else None,
                mother=self.mother)
            b = blk.get(symbol) or {}
            if self.source == "mother" and not (b.get("available")
                                                and b.get("bars")):
                return SRC_UNAVAILABLE
            return b.get("source") or "local:akshare"
        except Exception:                           # noqa: BLE001
            return ("local:akshare" if self.source != "mother"
                    else "unavailable")
