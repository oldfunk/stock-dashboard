"""回填历史日K（前复权），供历史估值分位 / 回测（2026-10-23 P0-5）。

默认拉取候选池 + 观察池 + 钉选；``--all`` 拉取所有有年报的股票（回测域）。
起点 2016-01-01（覆盖 10 年）。幂等：INSERT OR REPLACE 按 (code, trade_date)。

**断点续跑（2026-10-25）**：处理过的股票写入 ``backfill_progress``
（task=``price_history``），失败/无数据可用 ``--retry-no-data`` 重试。
审计实证：旧脚本只取候选 25 只，导致回测价格域仅 49 只、2016–2019 可选域为 0。

用法：
  python scripts/backfill_price_history.py                 # 候选池+观察池+钉选
  python scripts/backfill_price_history.py --all --limit 100 --sleep 0.3
  python scripts/backfill_price_history.py --codes 600519,000001
  python scripts/backfill_price_history.py --start 2016-01-01
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from src.logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from src.models.database import (
    init_database, db_conn, KlineDAO, FinancialHistoryDAO,
    ScreeningResultDAO, WatchlistDAO,
)
from src.models.ai_watchlist import AiWatchlistDAO
from src.collector.akshare_fetcher import fetch_kline_data

PROGRESS_TASK = "price_history"

_KLINE_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
              "?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
              "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
              "&klt=101&fqt=1&beg={beg}&end=20500101")


def _curl_json(url: str, timeout: int = 15):
    """curl 直连取 JSON（绕过 TUN 代理对 Python urllib/requests 的阻断）。

    项目既有先例：`curl_get`。这里补 UA/Referer 并做一次重试。
    """
    for attempt in range(2):
        try:
            r = subprocess.run(
                ["curl", "-s", "--connect-timeout", "6", "--max-time", str(timeout),
                 "-H", "User-Agent: Mozilla/5.0",
                 "-H", "Referer: https://quote.eastmoney.com/",
                 "-H", "Accept-Encoding: identity", url],
                capture_output=True, timeout=timeout + 5)
            if r.returncode == 0 and r.stdout:
                return json.loads(r.stdout.decode("utf-8", "ignore"))
        except Exception as e:  # noqa: BLE001 - 单次失败重试
            logger.debug(f"[P0-5] curl 失败: {e}")
        if attempt == 0:
            time.sleep(0.5)
    return None


def fetch_kline_curl(code: str, start_date: str) -> list[dict]:
    """东财前复权日K（curl 直连），返回与 ``fetch_kline_data`` 同结构。

    akshare 的 ``stock_zh_a_hist`` 在 Pi 上走 requests，被代理阻断后回退腾讯
    （不复权、约 30s/只）。curl 直连 push2his 约 0.3s/只，口径仍是前复权。
    """
    secid = ("1." if code.startswith(("6", "9")) else "0.") + code
    data = _curl_json(_KLINE_URL.format(secid=secid,
                                        beg=start_date.replace("-", "")))
    klines = ((data or {}).get("data") or {}).get("klines") or []
    out = []
    for line in klines:
        p = line.split(",")
        if len(p) < 7:
            continue
        try:
            out.append({
                "trade_date": p[0],
                "open": float(p[1]),
                "close": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "volume": float(p[5]),
                "amount": float(p[6]),
                "turnover": None,
            })
        except (TypeError, ValueError):
            continue
    return out


def target_codes() -> list[str]:
    codes: set[str] = set()
    try:
        codes.update(c['code'] for c in ScreeningResultDAO().get_latest_results(limit=25))
    except Exception as e:
        logger.warning(f"[P0-5] 候选池读取失败: {e}")
    try:
        codes.update(w['code'] for w in AiWatchlistDAO().get_all())
    except Exception as e:
        logger.warning(f"[P0-5] 观察池读取失败: {e}")
    try:
        codes.update(WatchlistDAO().get_watched_codes())
    except Exception as e:
        logger.warning(f"[P0-5] 钉选读取失败: {e}")
    return sorted(codes)


def fundamental_codes() -> list[str]:
    """所有有年报的股票（回测的基本面域）。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM financial_history"
            " WHERE report_type = 'A' ORDER BY stock_code").fetchall()
    return [r["stock_code"] for r in rows]


def pending_codes(codes: list[str], retry_no_data: bool = False) -> list[str]:
    """剔除已处理过的股票（断点续跑）；``no_data`` 默认也跳过。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT code, status FROM backfill_progress WHERE task = ?",
            (PROGRESS_TASK,)).fetchall()
    skip = {r["code"] for r in rows
            if r["status"] == "done" or (r["status"] == "no_data" and not retry_no_data)}
    return [c for c in codes if c not in skip]


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史日K")
    parser.add_argument("--codes", default=None, help="逗号分隔，缺省取候选+观察池+钉选")
    parser.add_argument("--all", action="store_true", help="所有有年报的股票（回测域）")
    parser.add_argument("--start", default="2016-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理股票数（0=不限）")
    parser.add_argument("--sleep", type=float, default=0.3, help="每只之间停顿秒数")
    parser.add_argument("--retry-no-data", action="store_true",
                        help="重试上次无数据的股票")
    parser.add_argument("--source", default="curl", choices=["curl", "akshare"],
                        help="价格源：curl=东财直连前复权（默认，快）；akshare=旧链路")
    args = parser.parse_args()

    init_database()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.all:
        codes = fundamental_codes()
    else:
        codes = target_codes()
    if not codes:
        print("无待回填代码")
        return 0
    codes = pending_codes(codes, args.retry_no_data)
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        print("无待回填代码（均已完成，--retry-no-data 可重试失败项）")
        return 0

    dao = KlineDAO()
    progress = FinancialHistoryDAO()
    fetch = fetch_kline_curl if args.source == "curl" else \
        (lambda code, start_date: fetch_kline_data(code, start_date=start_date,
                                                   adjust="qfq"))
    ok = 0
    total_rows = 0
    started = time.monotonic()
    for idx, code in enumerate(codes, 1):
        try:
            records = fetch(code, args.start)
            if records:
                n = dao.upsert_many(code, records)
                progress.mark_backfill_done(PROGRESS_TASK, code, n, "done")
                ok += 1
                total_rows += n
                logger.info(f"[P0-5] {idx}/{len(codes)} {code}: {n} 条"
                            f"（{records[0]['trade_date']}~{records[-1]['trade_date']}）")
            else:
                progress.mark_backfill_done(PROGRESS_TASK, code, 0, "no_data")
                logger.warning(f"[P0-5] {code}: 无数据")
        except Exception as e:
            progress.mark_backfill_done(PROGRESS_TASK, code, 0, "no_data")
            logger.warning(f"[P0-5] {code} 失败: {e}")
        if args.sleep:
            time.sleep(args.sleep)
    elapsed = round(time.monotonic() - started, 1)
    print(f"回填 {ok}/{len(codes)} 只，共 {total_rows} 条（{elapsed}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
