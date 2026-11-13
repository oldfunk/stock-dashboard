"""Sina 日线双拉 → 三列价格模型（批20-a 上移 src 层，2026-11-12）。

2026-10-26 首版在 ``scripts/backfill_price_history.py``；这里上移的理由与
``price_model``（U9-A）、``trading_calendar``（P2-1）完全相同：**周六维护线程要
接线 qfq 重锚，而 src 不允许反向 import scripts**（``tests/test_no_src_imports_scripts.py``
是机器可判的棘轮）。旧脚本保留同名 re-export，``backfill_price_depth`` /
``fix_price_contract`` 的 import 路径与既有 monkeypatch 点都不受影响。

口径与 ``akshare_fetcher`` 的东财/腾讯双拉一致：不复权 + 后复权各一次，交给同一个
``merge_price_model`` 合流，不在此处自行拼 ``close``/``close_qfq``（避免第二个口径来源）。
"""

from __future__ import annotations

import logging
import time

from src.collector.akshare_fetcher import SourceUnsupportedError
from src.collector.price_model import merge_price_model
from src.utils import market_of, sina_symbol

logger = logging.getLogger(__name__)


def _sina_symbol(code: str) -> str:
    """新浪格式：sh/sz/**bj**（实测 getKLineData?symbol=bj920000 有日线）。"""
    return sina_symbol(code)


def _sina_daily(code: str, adjust: str, start_date: str = "2016-01-01",
                end_date: str | None = None) -> list[dict]:
    """Sina 日线（akshare 封装）：adjust='' 不复权 / 'hfq' 后复权。

    Sina 同时返回每日 `outstanding_share`（流通股本），可用于历史每股指标。

    `end_date` 缺省=今天；批13-a 的深度回填只取**早段窗口**时显式给到库内最早日，
    少下载十一年尾巴（口径不变，只是右端提前收口）。
    """
    import akshare as ak
    from datetime import date as _date
    df = ak.stock_zh_a_daily(
        symbol=_sina_symbol(code),
        start_date=start_date.replace("-", ""),
        end_date=(end_date or _date.today().isoformat()).replace("-", ""),
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


def fetch_kline_sina_adjusted(code: str, start_date: str,
                              retries: int = 3,
                              end_date: str | None = None) -> list[dict]:
    """Sina 不复权 + 后复权 → 三列价格模型（2026-10-26）。

    单只约 3–5s×2；无东财式限流。失败退避 5/10/20s 重试。
    `end_date` 给定时只取到那一天（批13-a 深度回填取早段窗口用）。

    **批13-d：北交所是能力边界，不是"没数据"**。产线实测（2026-10-31，920305）：
    akshare 的 `stock_zh_a_daily` 对 bj 前缀给空壳，底层 `_sina_daily` 抛
    `JSONDecodeError: No value to decode`，本函数三次退避后包装成 RuntimeError——
    在 `price_history` 主循环里就成了 partial，每轮重跑一次"注定失败的 35s 退避"。
    这里在**打网络之前**抛 `SourceUnsupportedError`（镜像腾讯 fqkline 的 12-a 处理）：
    调用方按能力边界跳过，既不记 no_data（那会被进度表永久排除），也不再白等退避。
    守卫必须在重试循环**之外**：循环内是 `except Exception`，会把它吞掉。
    """
    if market_of(code) == "BJ":
        raise SourceUnsupportedError(
            f"Sina 日线（akshare stock_zh_a_daily）不支持北交所代码 {code}"
            "（实测 bj 前缀 No value to decode；BJ 日线走 --source akshare 的东财路径）")
    last_err = None
    for attempt in range(retries + 1):
        try:
            raw = _sina_daily(code, "", start_date, end_date)
            time.sleep(0.2)
            adj = _sina_daily(code, "hfq", start_date, end_date)
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