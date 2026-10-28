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
    """拉取并落库基准指数日线（价格指数）。返回 {code: rows}。

    除 config ``outcomes.benchmarks``（收益验证用）外，**固定包含子项目契约
    标准码**（Issue #5：000001/399001/399006）——它们是一次性脚本回填的，
    不进周刷就会在数天内 stale（审计 F3，同"有回填无调度"类）。
    """
    from src.collector.akshare_fetcher import (
        fetch_index_kline, fetch_index_kline_em,
    )
    from src.models.database import BenchmarkDailyDAO

    # 契约标准码 → 东财/新浪指数符号（与 scripts/fix_price_contract.py 一致）
    contract_sources = {"000001": "sh000001", "399001": "sz399001",
                        "399006": "sz399006"}
    stats = {}
    dao = BenchmarkDailyDAO()
    targets: dict[str, str] = {}   # 存储码 → 拉取符号
    for b in _benchmarks(config):
        targets[b["code"]] = b["code"]
    for code, src in contract_sources.items():
        targets.setdefault(code, src)
    for code, src in targets.items():
        try:
            # 东财优先：新浪的 sh000922 只更新到 2019 年，会导致超额全空
            records = fetch_index_kline_em(src, limit=limit)
            if not records:
                logger.warning(f"[收益验证] 东财无 {src} 日线，回退新浪")
                records = fetch_index_kline(src, limit=limit)
            saved = dao.upsert_many(code, records)
            stats[code] = saved
            logger.info(f"[收益验证] 基准 {code} 日线 {saved} 条")
        except Exception as e:
            logger.warning(f"[收益验证] 基准 {code} 拉取失败: {e}")
            stats[code] = 0
    return stats


def _mos_at_entry(fin_dao, code: str, analysis_date: str,
                  entry_price: float | None) -> float | None:
    """买入时安全边际 %（P0-9）：用 notice_date <= 分析日 的最新年报 + 入场价算模型 MOS。

    没有 ``notice_date`` 的年报不参与（**不回退 report_date**——会计年末与实际
    披露差约 1-4 个月，回退即前视；2026-10-28 N7 修，口径同 valuation_percentile）。
    """
    try:
        rows = fin_dao.get_annual_reports(code)
    except Exception:
        rows = []
    rec = None
    best_notice = None
    for r in rows:  # 按 report_date 倒序
        nd = r.get("notice_date")
        if nd and nd <= analysis_date and (best_notice is None or nd > best_notice):
            rec, best_notice = r, nd
    if rec is None or entry_price is None:
        return None
    from src.analyzer.valuation import intrinsic_value
    val = intrinsic_value({
        "fcf": rec.get("fcf"),
        "total_shares": rec.get("total_shares"),
        "cash": rec.get("cash"),
        "interest_bearing_debt": rec.get("interest_bearing_debt"),
    }, entry_price)
    return val.get("margin_of_safety")


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
        StockSnapshotDAO, FinancialHistoryDAO,
    )
    from src.collector.akshare_fetcher import fetch_kline_data
    from src.analyzer.valuation import intrinsic_value

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
    fin_dao = FinancialHistoryDAO()
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
                # P0-9：买入时安全边际（可复现模型；缺数据留空）
                merged["mos_at_entry"] = old.get("mos_at_entry")
                if (merged.get("mos_at_entry") is None
                        and merged.get("entry_price") is not None):
                    merged["mos_at_entry"] = _mos_at_entry(
                        fin_dao, code, a["analysis_date"], merged["entry_price"])
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


def summarize_by_mos(benchmark_code: str | None = None,
                     tiers=None,
                     horizons=(20, 60)) -> list[dict]:
    """按买入安全边际分层统计收益/超额/胜率（P0-9：验证 MOS 是否有预测力）。

    默认分层覆盖**全区间**（``≤0 / 0~20 / 20~40 / ≥40``，MOS 为百分数）。
    任何落在给定 ``tiers`` 之外的样本会以 ``未分层`` 档**显式列出**——
    历史教训：旧默认 ``(-100, 100)`` 曾静默丢弃 24% 样本（MOS 可低至 -523%），
    导致正档胜率被系统性高估。
    """
    from src.models.database import AnalysisOutcomeDAO
    if tiers is None:
        tiers = ((float("-inf"), 0), (0, 20), (20, 40), (40, float("inf")))
    rows = AnalysisOutcomeDAO().get_all(benchmark_code)
    with_mos = [r for r in rows if r.get("mos_at_entry") is not None]

    def _label(lo, hi) -> str:
        if lo == float("-inf"):
            return f"≤{hi}"
        if hi == float("inf"):
            return f"≥{lo}"
        return f"{lo}~{hi}"

    def _stat(sel: list[dict], label: str) -> dict:
        stat: dict = {"tier": label, "n": len(sel)}
        if sel:
            for h in horizons:
                rets = [r[f"ret_{h}"] for r in sel if r.get(f"ret_{h}") is not None]
                exc = [r[f"excess_{h}"] for r in sel if r.get(f"excess_{h}") is not None]
                stat[f"avg_ret_{h}"] = round(sum(rets) / len(rets), 2) if rets else None
                stat[f"avg_excess_{h}"] = round(sum(exc) / len(exc), 2) if exc else None
                stat[f"hit_{h}"] = (round(100.0 * sum(1 for v in rets if v > 0) / len(rets), 1)
                                    if rets else None)
        return stat

    out = []
    assigned = 0
    for lo, hi in tiers:
        sel = [r for r in with_mos if lo <= r["mos_at_entry"] < hi]
        assigned += len(sel)
        out.append(_stat(sel, _label(lo, hi)))

    # 兜底：区间外样本显式成档，绝不静默丢弃
    if assigned < len(with_mos):
        rest = [r for r in with_mos
                if not any(lo <= r["mos_at_entry"] < hi for lo, hi in tiers)]
        out.append(_stat(rest, "未分层"))
    return out
