"""全市场回填年报补充字段（2026-10-24，P0-3/4/10 覆盖扩展）。

背景：`cash / interest_bearing_debt / goodwill / total_assets / total_equity /
capex / net_operate_cashflow / notice_date` 此前只对候选池（~36 只）采集，
导致可复现估值与前视安全回测**只对候选池成立**。本脚本把这几项扩到全市场。

每只股票 3 类请求（东财直连，无需 akshare）：
- 资产负债表 F10（cash / 有息负债 / 商誉 / 总资产 / 净资产）
- 现金流量表 F10（capex / 经营现金流净额）
- 主要财务指标（notice_date / fcf / roic / total_share，一次取回全部年报）

**Pi 友好**：
- 逐只限速（`--sleep`，默认 0.5s），避免打爆上游与占满 Pi。
- 单次限量（`--limit`，默认 100）+ 软预算（`--max-seconds`，默认 600）到点优雅退出。
- **进度表断点续跑**：每只处理后写 `backfill_progress(task='financial_extra')`；
  已处理的股票不再重复出现（否则老股票因上游只回 10 年、老年份永远 NULL 而无限重跑）。
  `--retry-no-data` 可重试上次"无数据"的股票。
- fail-closed：请求失败/无数据一律跳过，绝不写 0 或占位值。

用法：
  python scripts/backfill_financial_extra.py --limit 100 --sleep 0.5
  python scripts/backfill_financial_extra.py --dry-run          # 只看目标规模
  python scripts/backfill_financial_extra.py --status           # 看进度
  python scripts/backfill_financial_extra.py --limit 50 --retry-no-data
"""

import argparse
import json
import logging
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from src.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from src.models.database import FinancialHistoryDAO, init_database


def collect_extra_for_code(code: str, bs_fetcher=None, cf_fetcher=None,
                           mf_fetcher=None) -> dict:
    """合并三个来源，返回 ``{report_date: {字段: 值}}``（只含有值的项）。

    任一路失败按空处理（不阻断其他来源，不写占位值）。
    """
    if bs_fetcher is None:
        from src.collector.balance_sheet_fetcher import fetch_balance_sheet_annual
        bs_fetcher = fetch_balance_sheet_annual
    if cf_fetcher is None:
        from src.collector.cashflow_fetcher import fetch_cash_flow_annual
        cf_fetcher = fetch_cash_flow_annual
    if mf_fetcher is None:
        from src.collector.mainfin_fetcher import fetch_mainfin_annual
        mf_fetcher = fetch_mainfin_annual

    def _safe(fn):
        try:
            return fn(code) or {}
        except Exception as e:  # noqa: BLE001 - 单源失败不影响其他源
            logger.debug(f"[extra] {code} 源失败: {e}")
            return {}

    bs = _safe(bs_fetcher)
    cf = _safe(cf_fetcher)
    mf = _safe(mf_fetcher)

    payload: dict[str, dict] = {}
    for src in (bs, cf, mf):
        for rpt, vals in src.items():
            bucket = payload.setdefault(rpt, {})
            for k, v in vals.items():
                if v is None:
                    continue
                # mainfin 的 total_share → 落库列名 total_shares
                bucket["total_shares" if k == "total_share" else k] = v
    return payload


def apply_code(dao: FinancialHistoryDAO, code: str, payload: dict) -> int:
    """把 payload 写进该股已存在的年报行（只填 NULL）；返回受影响行数。"""
    if not payload:
        return 0
    updated = 0
    for rpt, vals in payload.items():
        updated += dao.update_extra_fields(code, rpt, vals)
    return updated


TASK = "financial_extra"


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场回填年报补充字段")
    parser.add_argument("--limit", type=int, default=100, help="本次最多处理股票数")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 只（一般不用，进度表已续跑）")
    parser.add_argument("--sleep", type=float, default=0.5, help="每只之间停顿秒数")
    parser.add_argument("--max-seconds", type=float, default=600.0,
                        help="软预算：超过则优雅退出（0=不限）")
    parser.add_argument("--dry-run", action="store_true", help="只统计目标规模")
    parser.add_argument("--status", action="store_true", help="只看进度汇总")
    parser.add_argument("--retry-no-data", action="store_true",
                        help="连同上次无数据的股票一起重试")
    args = parser.parse_args()

    os.chdir(PROJ)          # 相对路径（data/db）以项目根为基准；只在 main 里生效，import 无副作用
    init_database()
    dao = FinancialHistoryDAO()

    if args.status:
        print(json.dumps(dao.backfill_progress_summary(TASK), ensure_ascii=False))
        return 0

    if args.dry_run:
        total = len(dao.get_codes_missing_extra(task=TASK))
        print(json.dumps({"missing_extra_codes": total}, ensure_ascii=False))
        return 0

    codes = dao.get_codes_missing_extra(limit=args.limit, offset=args.offset,
                                        task=TASK,
                                        retry_no_data=args.retry_no_data)
    logger.info(f"[extra] 目标 {len(codes)} 只（limit={args.limit}, retry_no_data={args.retry_no_data}）")

    started = time.monotonic()
    processed = updated_rows = no_data = 0
    for idx, code in enumerate(codes):
        if args.max_seconds and (time.monotonic() - started) > args.max_seconds:
            logger.info(f"[extra] 达到软预算 {args.max_seconds}s，优雅退出（已处理 {processed}）")
            break
        payload = collect_extra_for_code(code)
        n = apply_code(dao, code, payload) if payload else 0
        if payload:
            updated_rows += n
        else:
            no_data += 1
        # 进度落表：无论有无数据都标记，避免无限重跑（no_data 可 --retry-no-data 重试）
        dao.mark_backfill_done(TASK, code, n, "done" if payload else "no_data")
        processed += 1
        if idx % 20 == 0 or idx == len(codes) - 1:
            logger.info(f"[extra] {idx + 1}/{len(codes)} {code} "
                        f"（更新行 {updated_rows}，无数据 {no_data}）")
        if args.sleep:
            time.sleep(args.sleep)

    elapsed = round(time.monotonic() - started, 1)
    summary = {
        "processed": processed,
        "updated_rows": updated_rows,
        "no_data": no_data,
        "elapsed_sec": elapsed,
        "progress": dao.backfill_progress_summary(TASK),
        "remaining": len(dao.get_codes_missing_extra(task=TASK)),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
