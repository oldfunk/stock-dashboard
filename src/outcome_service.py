"""信号前向收益刷新服务（2026-10-21）。

职责边界：
- 采集/网络：拉基准指数与个股前复权日线。
- 计算：调用 src.analyzer.outcomes 的纯函数。
- 落库：analysis_outcome / benchmark_daily。
不做交易、不改筛选、不发通知。
"""

import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _benchmarks(config: dict | None) -> list[dict]:
    from src.analyzer.outcomes import DEFAULT_BENCHMARKS
    cfg = (config or {}).get("outcomes", {})
    items = cfg.get("benchmarks") or DEFAULT_BENCHMARKS
    out = []
    for b in items:
        if isinstance(b, dict) and b.get("code"):
            out.append({"code": b["code"],
                        "name": b.get("name") or b["code"]})
    return out or DEFAULT_BENCHMARKS


def _horizons(config: dict | None) -> tuple[int, ...]:
    from src.analyzer.outcomes import HORIZONS
    raw = (config or {}).get("outcomes", {}).get("horizons") or HORIZONS
    try:
        vals = tuple(int(x) for x in raw if int(x) > 0)
    except (TypeError, ValueError):
        vals = HORIZONS
    return vals or HORIZONS


def refresh_benchmarks(config: dict | None = None,
                       limit: int = 6000) -> dict:
    """拉取并落库基准指数日线（价格指数）。返回 {code: rows}。"""
    from src.collector.akshare_fetcher import (
        fetch_index_kline, fetch_index_kline_em,
    )
    from src.models.database import BenchmarkDailyDAO

    stats = {}
    dao = BenchmarkDailyDAO()
    for b in _benchmarks(config):
        code = b["code"]
        try:
            # 东财优先：新浪的 sh000922 只更新到 2019 年，会导致超额全空
            records = fetch_index_kline_em(code, limit=limit)
            if not records:
                logger.warning(f"[收益验证] 东财无 {code} 日线，回退新浪")
                records = fetch_index_kline(code, limit=limit)
            saved = dao.upsert_many(code, records)
            stats[code] = saved
            logger.info(f"[收益验证] 基准 {code} 日线 {saved} 条")
        except Exception as e:
            logger.warning(f"[收益验证] 基准 {code} 拉取失败: {e}")
            stats[code] = 0
    return stats


def _is_complete(existing: dict | None, horizons: tuple[int, ...]) -> bool:
    if not existing:
        return False
    for h in horizons:
        if existing.get(f"ret_{h}") is None:
            return False
        if existing.get(f"bench_{h}") is None:
            return False
        if existing.get(f"excess_{h}") is None:
            return False
    return True


def refresh_outcomes(config: dict | None = None,
                     limit: int | None = None,
                     dry_run: bool = False) -> dict:
    """为全部分析记录补算前向收益；已算出的值不重算（冻结）。"""
    from src.analyzer.outcomes import (
        VALID_SIGNALS, compute_forward_outcome, extract_signal,
    )
    from src.models.database import (
        AnalysisOutcomeDAO, BenchmarkDailyDAO, StockAnalysisHistoryDAO,
        StockSnapshotDAO,
    )
    from src.collector.akshare_fetcher import fetch_kline_data

    config = config or {}
    horizons = _horizons(config)
    benches = _benchmarks(config)
    hist_dao = StockAnalysisHistoryDAO()
    out_dao = AnalysisOutcomeDAO()
    bench_series = {
        b["code"]: BenchmarkDailyDAO().get_series(b["code"])
        for b in benches
    }
    for b in benches:
        if not bench_series[b["code"]]:
            logger.warning(f"[收益验证] 基准 {b['code']} 无日线，"
                           f"本次只算个股收益，超额留空")

    raw = hist_dao.list_all()
    if limit:
        raw = raw[-limit:]
    # 老分析的 ai_trade_strategy 可能是空 JSON；没有 Signal 就没有可验证的方向
    analyses = []
    no_signal = 0
    for a in raw:
        signal, _, _ = extract_signal(a)
        if signal not in VALID_SIGNALS:
            no_signal += 1
            continue
        analyses.append(a)
    if not analyses:
        deleted = 0 if dry_run else out_dao.delete_without_signal()
        return {"analyses": 0, "stocks": 0, "rows": 0, "skipped": 0,
                "complete": 0, "fetched": 0, "no_signal": no_signal,
                "deleted": deleted}

    by_code: dict[str, list[dict]] = {}
    for a in analyses:
        by_code.setdefault(a["stock_code"], []).append(a)

    snap_dao = StockSnapshotDAO()
    names: dict[str, str] = {}
    rows_to_save: list[dict] = []
    fetched = skipped = complete = 0

    for code, items in by_code.items():
        # 先看是否全部已完整，省掉网络请求
        pending = []
        for a in items:
            existing = {
                b["code"]: out_dao.get(a["id"], b["code"]) for b in benches
            }
            if all(_is_complete(existing[b["code"]], horizons) for b in benches):
                complete += 1
                continue
            pending.append((a, existing))
        if not pending:
            continue

        earliest = min(a["analysis_date"] for a, _ in pending)
        try:
            start = (datetime.strptime(earliest[:10], "%Y-%m-%d")
                     - timedelta(days=7)).strftime("%Y-%m-%d")
        except ValueError:
            start = None
        try:
            stock_bars = fetch_kline_data(code, start_date=start, adjust="qfq")
            fetched += 1
        except Exception as e:
            logger.warning(f"[收益验证] {code} K线拉取失败: {e}")
            stock_bars = []
        time.sleep(0.3)  # 温和限流，34 只约 10 秒

        if not stock_bars:
            skipped += len(pending)
            continue

        if code not in names:
            snap = snap_dao.get_by_code(code)
            names[code] = (snap or {}).get("name") or ""

        for a, existing in pending:
            signal, confidence, verdict = extract_signal(a)
            for b in benches:
                old = existing.get(b["code"]) or {}
                computed = compute_forward_outcome(
                    a["analysis_date"], stock_bars,
                    bench_series.get(b["code"]) or [], horizons)
                # 冻结：已有非空值优先；只补空位
                merged = {
                    "analysis_id": a["id"],
                    "benchmark_code": b["code"],
                    "stock_code": code,
                    "stock_name": names.get(code) or old.get("stock_name") or "",
                    "analysis_date": a["analysis_date"],
                    "signal": signal,
                    "confidence": confidence,
                    "verdict": verdict,
                }
                for k in ("entry_date", "entry_price"):
                    merged[k] = old.get(k) if old.get(k) is not None else computed[k]
                for h in horizons:
                    for prefix in ("ret", "bench", "excess"):
                        key = f"{prefix}_{h}"
                        merged[key] = (old.get(key) if old.get(key) is not None
                                       else computed.get(key))
                if merged.get("entry_date") is None and not old:
                    skipped += 1
                    continue
                rows_to_save.append(merged)

    deleted = 0
    if not dry_run:
        if rows_to_save:
            out_dao.upsert_many(rows_to_save)
        deleted = out_dao.delete_without_signal()

    logger.info(f"[收益验证] 分析 {len(analyses)} 条（无Signal {no_signal}），"
                f"股票 {len(by_code)} 只，拉K线 {fetched} 只，"
                f"写 {len(rows_to_save)} 行，跳过 {skipped}，"
                f"已完整 {complete}，清理占位 {deleted}")
    return {
        "analyses": len(analyses),
        "stocks": len(by_code),
        "rows": len(rows_to_save),
        "skipped": skipped,
        "complete": complete,
        "fetched": fetched,
        "no_signal": no_signal,
        "deleted": deleted,
    }
