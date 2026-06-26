"""
股票看板 - 后台实时调度器

在 Web 服务进程中运行，负责：
1. 盘中定时轮询大盘指数（30分钟）
2. 盘中定时轮询选股池实时行情（5分钟）
3. 每日 15:30 自动触发选股流水线

所有实时数据通过 /api/realtime 接口推送给前端。
"""

import logging
import threading
import time
import subprocess
import json
import re
from datetime import datetime, date, time as dtime
from typing import Optional

logger = logging.getLogger(__name__)

# ── 实时价格缓存（线程安全） ──
_realtime_cache: dict = {}
_cache_lock = threading.Lock()

# ── 活跃选股代码（由 routes 在启动时注入） ──
_active_codes: list[str] = []


def set_active_codes(codes: list[str]):
    global _active_codes
    _active_codes = codes


def get_realtime_cache() -> dict:
    """获取实时行情快照（线程安全）"""
    with _cache_lock:
        return dict(_realtime_cache)


# ── 工具函数 ──

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(val)
        import math
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 2)
    except (ValueError, TypeError):
        return None


def _curl_get(url: str, timeout=20) -> Optional[str]:
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', '6',
             '--max-time', str(timeout), url],
            capture_output=True, timeout=timeout + 5)
        if r.returncode == 0 and r.stdout:
            try:
                return r.stdout.decode('utf-8')
            except UnicodeDecodeError:
                return r.stdout.decode('gbk', errors='replace')
    except Exception:
        pass
    return None


def _is_trading_time() -> bool:
    """A股交易时间判断：周一至周五 9:30-11:30, 13:00-15:00"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周六日
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30) or
            dtime(13, 0) <= t <= dtime(15, 0))


def _tc_encode(code: str) -> str:
    c = code.strip()
    if c.startswith('6') or c.startswith('9'):
        return f"sh{c}"
    return f"sz{c}"


def _parse_tc_line(raw: str) -> Optional[dict]:
    """解析腾讯行情行"""
    try:
        parts = raw.split('~')
        if len(parts) < 50:
            return None
        code = parts[2].strip()
        return {
            'code': code,
            'name': parts[1].strip(),
            'current_price': _safe_float(parts[3]),
            'prev_close': _safe_float(parts[4]),
            'open_price': _safe_float(parts[5]),
            'high': _safe_float(parts[33]),
            'low': _safe_float(parts[34]),
            'change_percent': _safe_float(parts[32]),
            'change_amount': _safe_float(parts[31]),
            'volume': _safe_float(parts[6]),
            'amount': _safe_float(parts[37]),
            'pe': _safe_float(parts[39]) if len(parts) > 39 else None,
            'pb': _safe_float(parts[46]) if len(parts) > 46 else None,
            'market_cap': _safe_float(parts[45]) if len(parts) > 45 else None,
            'amplitude': _safe_float(parts[43]) if len(parts) > 43 else None,
            'turnover_rate': _safe_float(parts[38]) if len(parts) > 38 else None,
        }
    except Exception:
        return None


# ── 数据采集任务 ──

def fetch_stock_realtime(codes: list[str]) -> dict[str, dict]:
    """批量获取股票实时行情"""
    if not codes:
        return {}
    tc_codes = [_tc_encode(c) for c in codes]
    batch_size = 80
    results: dict[str, dict] = {}
    for i in range(0, len(tc_codes), batch_size):
        batch = tc_codes[i:i + batch_size]
        raw = _curl_get(f"https://qt.gtimg.cn/q={','.join(batch)}", timeout=20)
        if raw:
            for line in raw.strip().split('\n'):
                if '=' not in line:
                    continue
                line = line.split(';')[0]
                eq = line.find('=')
                if eq < 0:
                    continue
                val = line[eq + 1:].strip().strip('"')
                q = _parse_tc_line(val)
                if q:
                    results[q['code']] = q
        if i + batch_size < len(tc_codes):
            time.sleep(0.3)
    return results


def fetch_indices_realtime() -> list[dict]:
    """获取大盘指数实时行情（腾讯 qt.gtimg.cn，与选股行情统一接口）"""
    now = datetime.now()
    ds, ts = now.strftime("%Y-%m-%d"), now.isoformat()
    tc_codes = ['sh000001', 'sz399001', 'sz399006', 'sh000688']
    target_map = {
        'sh000001': '上证指数', 'sz399001': '深证成指',
        'sz399006': '创业板指', 'sh000688': '科创50',
    }
    raw = _curl_get(
        f"https://qt.gtimg.cn/q={','.join(tc_codes)}", timeout=15)
    if not raw:
        return []
    indices = []
    for line in raw.strip().split('\n'):
        if '=' not in line:
            continue
        line = line.split(';')[0]
        eq = line.find('=')
        if eq < 0:
            continue
        val = line[eq + 1:].strip().strip('"')
        parts = val.split('~')
        if len(parts) < 35:
            continue
        code = parts[2].strip()
        tc_key = f"{'sh' if code.startswith('00') else 'sz'}{code}"
        name = target_map.get(tc_key)
        if not name:
            continue
        cur = _safe_float(parts[3])
        yes_close = _safe_float(parts[4])
        chg_pct = ((cur - yes_close) / yes_close * 100) if (
            cur and yes_close and yes_close != 0) else None
        chg_amt = (cur - yes_close) if (cur and yes_close) else None
        volume = _safe_float(parts[6]) if len(parts) > 6 else None
        amount = _safe_float(parts[37]) if len(parts) > 37 else None
        indices.append({
            'index_code': tc_key,
            'index_name': name,
            'current_value': cur or 0,
            'change_percent': _safe_float(chg_pct),
            'change_amount': _safe_float(chg_amt),
            'volume': volume or 0,
            'amount': amount or 0,
            'pe': None, 'pb': None,
            'timestamp': ts, 'date': ds,
        })
    if indices:
        logger.info(f"[采集] 大盘: {len(indices)} 条")
    return indices


# ── 调度器 ──

class MarketScheduler:
    """后台行情调度器，作为 daemon 线程运行"""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 配置
        self.index_interval = 30 * 60       # 大盘轮询间隔（秒）
        self.stock_interval = 5 * 60        # 选股池轮询间隔（秒）
        self.daily_time = dtime(15, 30)     # 每日自动运行时间
        self._last_index_time = 0.0
        self._last_stock_time = 0.0
        self._last_daily_date = datetime.now().date()  # 启动时不触发当日流水线

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # 首次启动时立即从数据库加载选股代码并抓行情
        global _active_codes
        if not _active_codes:
            try:
                from src.models.database import ScreeningResultDAO
                stocks = ScreeningResultDAO().get_latest_results()
                _active_codes = [s['code'] for s in stocks]
            except Exception:
                pass
        self._poll_indices()
        self._poll_stocks(force=True)
        logger.info("[调度器] 已启动")

    def stop(self):
        self._stop.set()
        logger.info("[调度器] 已停止")

    def _run(self):
        while not self._stop.is_set():
            try:
                now = time.time()
                # 大盘指数（不限交易时间）
                if now - self._last_index_time >= self.index_interval:
                    self._poll_indices()
                    self._last_index_time = now

                # 选股池实时行情（仅交易时间）
                if now - self._last_stock_time >= self.stock_interval:
                    if _is_trading_time():
                        self._poll_stocks()
                    self._last_stock_time = now

                # 每日收盘流水线（交易日 15:30 后触发一次）
                self._check_daily_pipeline()

            except Exception as e:
                logger.warning(f"[调度器] 运行异常: {e}")
            # 每 30 秒检查一次停止标志
            self._stop.wait(30)

    def _poll_indices(self):
        """轮询大盘指数并存入数据库"""
        from src.models.database import MarketIndexDAO
        try:
            indices = fetch_indices_realtime()
            if indices:
                MarketIndexDAO().save(indices)
                names = [i['index_name'] for i in indices]
                logger.info(f"[调度器] 大盘更新: {', '.join(names)}")
        except Exception as e:
            logger.warning(f"[调度器] 大盘轮询失败: {e}")

    def _poll_stocks(self, force=False):
        """轮询选股池实时行情并更新缓存"""
        global _realtime_cache, _active_codes
        codes = _active_codes
        if not codes:
            return
        # 非强制模式下，非交易时间跳过
        if not force and not _is_trading_time():
            return
        try:
            quotes = fetch_stock_realtime(codes)
            if quotes:
                with _cache_lock:
                    _realtime_cache.update(quotes)
                logger.info(
                    f"[调度器] 实时行情更新: {len(quotes)} 只")
        except Exception as e:
            logger.warning(f"[调度器] 行情轮询失败: {e}")

    def _check_daily_pipeline(self):
        """检查是否需要触发每日流水线"""
        now = datetime.now()
        today = now.date()
        # 交易日 + 时间超过 15:30 + 当天未运行过
        if now.weekday() >= 5:
            return
        if now.time() < self.daily_time:
            return
        if self._last_daily_date == today:
            return

        logger.info("[调度器] 触发每日选股流水线...")
        try:
            from src.orchestrator import run_daily_pipeline
            from src.orchestrator import load_config
            config = load_config()
            run_daily_pipeline(config)
            self._last_daily_date = today
            logger.info("[调度器] 每日流水线完成")
        except Exception as e:
            logger.warning(f"[调度器] 每日流水线失败: {e}")
