"""akshare 数据采集器。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

from paper_trading.models import Bar
from paper_trading.utils import get_logger

logger = get_logger(__name__)


class AkshareFetcher:
    """使用 akshare 获取 A 股日线数据（新浪优先，东财 fallback）。"""

    @staticmethod
    def _sina_symbol(symbol: str) -> str:
        if symbol.startswith("6"):
            return f"sh{symbol}"
        if symbol.startswith(("8", "4")):
            return f"bj{symbol}"
        return f"sz{symbol}"

    @staticmethod
    def _rows_to_bars(symbol: str, df) -> list[Bar]:
        bars: list[Bar] = []
        for _, row in df.iterrows():
            try:
                turn = float(row.get("turnover", row.get("turn", 0.0) or 0.0))
            except Exception:
                turn = 0.0
            bars.append(Bar(
                symbol=symbol,
                timestamp=pd.to_datetime(row["date"]).to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                turn=turn,
            ))
        return bars

    @staticmethod
    def fetch_daily(
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "qfq",
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> list[Bar]:
        """
        获取单只股票日线数据（前复权），带重试机制。

        Args:
            symbol: 股票代码，如 '600519'
            start_date: 起始日期 'YYYYMMDD'，默认近3年
            end_date: 结束日期 'YYYYMMDD'，默认今天
            adjust: 复权方式，'qfq'=前复权, 'hfq'=后复权, ''=不复权
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）

        Returns:
            Bar 列表
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        logger.info(f"Fetching {symbol} from {start_date} to {end_date} (adjust={adjust})")

        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                # 1) 新浪源（稳定优先），2) 东财源 fallback
                sina_symbol = AkshareFetcher._sina_symbol(symbol)
                try:
                    df = ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_date,
                                            end_date=end_date, adjust=adjust)
                except Exception as e:
                    last_err = e
                    logger.warning(f"Sina source failed for {symbol}: {e}, fallback to eastmoney")
                    df = ak.stock_zh_a_hist(symbol=symbol, start_date=start_date,
                                           end_date=end_date, adjust=adjust)
                if df is None or (hasattr(df, "empty") and df.empty):
                    logger.warning(f"No data returned for {symbol}")
                    return []

                # 东财列名兼容：日期/开高低收/成交量/换手率
                rename = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
                          "收盘": "close", "成交量": "volume", "换手率": "turnover"}
                for k, v in rename.items():
                    if k in df.columns and v not in df.columns:
                        df = df.rename(columns={k: v})
                bars = AkshareFetcher._rows_to_bars(symbol, df)
                logger.info(f"Fetched {len(bars)} bars for {symbol}")
                return bars
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    logger.warning(f"Attempt {attempt} failed for {symbol}: {e}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"All {max_retries} attempts failed for {symbol}: {last_err}")
                    raise

    @staticmethod
    def fetch_stock_names(symbols: list[str]) -> dict[str, str]:
        """
        获取真实股票名称 {symbol: 名称}。
        先走全市场代码-名称表（一次调用），失败再逐只兑底。
        """
        out: dict[str, str] = {}

        def _clean(nm) -> str:
            # akshare 代码表偶带字间空格（如“五 粮 液”），A 股简称无合法空格，直接压掉
            return "".join(str(nm).split())

        want = set(symbols)
        try:
            df = ak.stock_info_a_code_name()
            code_col = "code" if "code" in df.columns else df.columns[0]
            name_col = "name" if "name" in df.columns else df.columns[1]
            for _, row in df.iterrows():
                code = str(row[code_col]).strip()
                if code in want:
                    out[code] = _clean(row[name_col])
        except Exception as e:
            logger.warning(f"stock_info_a_code_name failed: {e}")
        for sym in symbols:
            if sym in out:
                continue
            try:
                info = ak.stock_individual_info_em(symbol=sym)
                d = dict(zip(info["item"], info["value"]))
                nm = d.get("股票简称")
                if nm:
                    out[sym] = _clean(nm)
            except Exception as e:
                logger.warning(f"Name lookup failed for {sym}: {e}")
        return out

    @staticmethod
    def fetch_stock_pool(
        symbols: list[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, list[Bar]]:
        """批量获取股票池数据。"""
        result: dict[str, list[Bar]] = {}
        for sym in symbols:
            try:
                result[sym] = AkshareFetcher.fetch_daily(sym, start_date, end_date)
            except Exception as e:
                logger.error(f"Failed to fetch {sym}: {e}")
                result[sym] = []
        return result
