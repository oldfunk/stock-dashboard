"""回填历史日K（2026-10-26 三列价格模型）。

**模型**（解决"前复权负价 / 不复权送转假跌 / 单列无法同时服务估值与回测"）：
- ``close_raw``：不复权收盘（估值 PE/PB 用）
- ``close_adj``：后复权收盘（回测总收益用，恒正）
- ``close``：后复权按最新价缩放 = 正数前复权（展示用；今日价 = 真实价）

**源**：Sina（akshare ``stock_zh_a_daily``）一次返回全历史 + 每日流通股本；
不复权/后复权各一次调用（约 3–5s），无东财式 35 次限流。东财/腾讯保留为备选。

默认拉取候选池 + 观察池 + 钉选；``--all`` 拉取所有有年报的股票（回测域）。
起点 2016-01-01。幂等：按 (code, trade_date) upsert，raw/adj 不会被旧调用清空。

**断点续跑**：处理过的股票写入 ``backfill_progress``（task=``price_history``）；
失败/无数据可用 ``--retry-no-data`` 重试。

用法：
  python scripts/backfill_price_history.py --all --sleep 0.5
  python scripts/backfill_price_history.py --codes 600519,000001
  python scripts/backfill_price_history.py --source tencent --all
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date, timedelta

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

_TX_KLINE_URL = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                 "?param={sym},day,{start},{end},{count},qfq")


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


def _tx_symbol(code: str) -> str:
    return f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"


def _tx_get_json(url: str, timeout: int = 15):
    """腾讯行情 JSON（requests 可直连；curl 到该域名被拒，实测 2026-10-26）。"""
    import requests
    headers = {"User-Agent": "Mozilla/5.0",
               "Referer": "https://gu.qq.com/"}
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"腾讯 K 线接口失败: {last}")


def fetch_kline_tencent_qfq(code: str, start_date: str,
                            page: int = 640) -> list[dict]:
    """腾讯前复权日K，分页拉全历史（每页最多 640 个交易日）。

    2026-10-26：东财 push2his 约 35 次请求后限流，改用腾讯 `fqkline`：
    Python requests 可直连、返回前复权、无频率问题；10 年约 5 页/只。
    """
    sym = _tx_symbol(code)
    start = start_date
    end = date.today().isoformat()
    out: dict[str, dict] = {}
    for _ in range(12):
        data = _tx_get_json(_TX_KLINE_URL.format(
            sym=sym, start=start, end=end, count=page))
        node = (data.get("data") or {}).get(sym) or {}
        rows = node.get("qfqday") or node.get("day") or []
        if not rows:
            break
        for r in rows:
            if len(r) < 6:
                continue
            try:
                out[r[0]] = {
                    "trade_date": r[0],
                    "open": float(r[1]),
                    "close": float(r[2]),
                    "high": float(r[3]),
                    "low": float(r[4]),
                    "volume": float(r[5]),
                    "amount": None,
                    "turnover": None,
                }
            except (TypeError, ValueError):
                continue
        first = rows[0][0]
        if first <= start or len(rows) < page:
            break
        end = (date.fromisoformat(first) - timedelta(days=1)).isoformat()
        time.sleep(0.15)
    return [out[d] for d in sorted(out)]


def _sina_symbol(code: str) -> str:
    return f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"


def _sina_daily(code: str, adjust: str, start_date: str = "2016-01-01") -> list[dict]:
    """Sina 日线（akshare 封装）：adjust='' 不复权 / 'hfq' 后复权。

    Sina 同时返回每日 `outstanding_share`（流通股本），可用于历史每股指标。
    """
    import akshare as ak
    from datetime import date as _date
    df = ak.stock_zh_a_daily(
        symbol=_sina_symbol(code),
        start_date=start_date.replace("-", ""),
        end_date=_date.today().strftime("%Y%m%d"),
        adjust=adjust,
    )
    if df is None or len(df) == 0:
        return []
    rows = []
    for _, r in df.iterrows():
        d = str(r.get("date"))[:10]
        if not d:
            continue

        def num(key):
            v = r.get(key)
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return None if v != v else v  # NaN

        rows.append({
            "trade_date": d,
            "open": num("open"), "close": num("close"),
            "high": num("high"), "low": num("low"),
            "volume": num("volume"), "amount": num("amount"),
            "turnover": num("turnover"), "shares": num("outstanding_share"),
        })
    return rows


def merge_price_model(raw_rows: list[dict], adj_rows: list[dict]) -> list[dict]:
    """合并不复权与后复权，生成三列价格。

    - ``close_raw``：不复权（估值 PE/PB 用）
    - ``close_adj``：后复权（回测总收益用，恒正）
    - ``close``：后复权按最新价缩放（= 正数前复权，展示用；不再出现负价）
    """
    raw = {r["trade_date"]: r for r in raw_rows if r.get("close") is not None}
    adj = {r["trade_date"]: r for r in adj_rows if r.get("close") is not None}
    dates = sorted(set(raw) & set(adj))
    if not dates:
        return []
    last = dates[-1]
    if not adj[last]["close"]:
        return []
    scale = raw[last]["close"] / adj[last]["close"]
    out = []
    for d in dates:
        a, r = adj[d], raw[d]
        out.append({
            "trade_date": d,
            "open": a["open"] * scale if a.get("open") is not None else None,
            "close": a["close"] * scale,
            "high": a["high"] * scale if a.get("high") is not None else None,
            "low": a["low"] * scale if a.get("low") is not None else None,
            "volume": r.get("volume"),
            "amount": r.get("amount"),
            "turnover": r.get("turnover"),
            "close_raw": r["close"],
            "close_adj": a["close"],
            "shares": r.get("shares"),
        })
    return out


def fetch_kline_sina_adjusted(code: str, start_date: str,
                              retries: int = 3) -> list[dict]:
    """Sina 不复权 + 后复权 → 三列价格模型（2026-10-26）。

    单只约 3–5s×2；无东财式限流。失败退避 5/10/20s 重试。
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            raw = _sina_daily(code, "", start_date)
            time.sleep(0.2)
            adj = _sina_daily(code, "hfq", start_date)
            rows = merge_price_model(raw, adj)
            if rows:
                return rows
            last_err = RuntimeError("Sina 返回空数据")
        except Exception as e:  # noqa: BLE001
            last_err = e
        if attempt < retries:
            wait = 5 * (2 ** attempt)
            logger.warning(f"[P0-5] {code} Sina 失败（{last_err}），退避 {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Sina 价格抓取失败: {last_err}")


def fetch_kline_curl(code: str, start_date: str, retries: int = 3) -> list[dict]:
    """东财前复权日K（curl 直连），返回与 ``fetch_kline_data`` 同结构。

    akshare 的 ``stock_zh_a_hist`` 在 Pi 上走 requests，被代理阻断后回退腾讯
    （不复权、约 30s/只）。curl 直连 push2his 约 0.3s/只，口径仍是前复权。

    2026-10-26 实测：接口不是永久封禁，而是**约 35 次快速请求后限流**。
    因此失败时指数退避（15/30/45s）重试，而不是直接放弃；仍失败才抛
    ``RuntimeError``（记 partial，可重试）。
    """
    secid = ("1." if code.startswith(("6", "9")) else "0.") + code
    url = _KLINE_URL.format(secid=secid, beg=start_date.replace("-", ""))
    data = None
    for attempt in range(retries + 1):
        data = _curl_json(url)
        if data is not None:
            break
        if attempt < retries:
            wait = 15 * (attempt + 1)
            logger.warning(f"[P0-5] {code} 接口无响应（限流/网络），退避 {wait}s 重试")
            time.sleep(wait)
    if data is None:
        raise RuntimeError("东财 K 线接口无响应（限流/网络）")
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
    parser.add_argument("--source", default="sina",
                        choices=["sina", "curl", "tencent", "akshare"],
                        help="价格源：sina=不复权+后复权（推荐，含每日股本）；curl=东财；tencent=腾讯；akshare=旧链路")
    parser.add_argument("--max-consecutive-failures", type=int, default=5,
                        help="连续失败达到 N 只即中止（防上游限流时把全市场标成失败）")
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
    if args.source == "sina":
        fetch = fetch_kline_sina_adjusted
    elif args.source == "curl":
        fetch = fetch_kline_curl
    elif args.source == "tencent":
        fetch = fetch_kline_tencent_qfq
    else:
        fetch = (lambda code, start_date: fetch_kline_data(
            code, start_date=start_date, adjust="qfq"))
    ok = 0
    total_rows = 0
    consecutive_failures = 0
    aborted = False
    started = time.monotonic()
    for idx, code in enumerate(codes, 1):
        try:
            records = fetch(code, args.start)
            if records:
                n = dao.upsert_many(code, records)
                progress.mark_backfill_done(PROGRESS_TASK, code, n, "done")
                ok += 1
                total_rows += n
                consecutive_failures = 0
                logger.info(f"[P0-5] {idx}/{len(codes)} {code}: {n} 条"
                            f"（{records[0]['trade_date']}~{records[-1]['trade_date']}）")
            else:
                progress.mark_backfill_done(PROGRESS_TASK, code, 0, "no_data")
                consecutive_failures += 1
                logger.warning(f"[P0-5] {code}: 无数据")
        except Exception as e:
            progress.mark_backfill_done(PROGRESS_TASK, code, 0, "partial")
            consecutive_failures += 1
            logger.warning(f"[P0-5] {code} 失败（可重试）: {e}")
        if args.max_consecutive_failures and \
                consecutive_failures >= args.max_consecutive_failures:
            logger.error(f"[P0-5] 连续 {consecutive_failures} 只失败，中止本轮"
                         f"（上游可能限流；稍后直接重跑即可续传）")
            aborted = True
            break
        if args.sleep:
            time.sleep(args.sleep)
    elapsed = round(time.monotonic() - started, 1)
    print(f"回填 {ok}/{len(codes)} 只，共 {total_rows} 条（{elapsed}s）"
          + ("；连续失败已中止" if aborted else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
