"""手动回测引擎（v1）：日期区间 replay，结果 inline 返回。

- 账本写临时文件（`tempfile`，系统临时目录，不在仓库内），返回后删除，主库零写入。
- T+1：每天先解冻再交易（与子项目 run_daily 顺序一致）；解冻日走 K 线实际交易日。
- 结果进进程内 `_RESULTS`（最多 10 个，FIFO），页面轮询取。
"""
import logging
import os
import tempfile
import threading
from collections import deque
from datetime import datetime

logger = logging.getLogger(__name__)

_RESULTS = {}
_RESULTS_LOCK = threading.Lock()
_SEQ = [0]
_MAX_KEPT = 10


def _next_id():
    with _RESULTS_LOCK:
        _SEQ[0] += 1
        return f"bt{_SEQ[0]:04d}"


def get_backtest(bid):
    with _RESULTS_LOCK:
        return _RESULTS.get(bid)


def _store(result):
    with _RESULTS_LOCK:
        _RESULTS[result["backtest_id"]] = result
        while len(_RESULTS) > _MAX_KEPT:
            _RESULTS.pop(sorted(_RESULTS)[0], None)


def load_scores(target_date, limit=20):
    """某日及之前最新一轮 Top（active，score 降序）。"""
    from src.models.database import db_conn
    with db_conn() as conn:
        row = conn.execute(
            "SELECT run_id FROM screening_result WHERE run_date <= ? "
            "ORDER BY run_date DESC, run_id DESC LIMIT 1",
            (target_date,)).fetchone()
        if not row:
            return []
        rows = conn.execute(
            "SELECT code, score FROM screening_result WHERE run_id = ? "
            "AND (status IS NULL OR status = 'active') "
            "ORDER BY score DESC LIMIT ?",
            (row["run_id"], limit)).fetchall()
    return [(r["code"], r["score"]) for r in rows]


def load_bars(codes, start, end):
    """从 kline_daily 取区间 K 线（trade_date 字符串可比）。"""
    from src.models.database import KlineDAO
    from src.paper.types import Bar
    dao = KlineDAO()
    out = {}
    for c in codes:
        bl = []
        for r in dao.get_daily(c, limit=5000):
            if not (start <= r["trade_date"] <= end):
                continue
            bl.append(Bar(symbol=c,
                          timestamp=datetime.fromisoformat(r["trade_date"]),
                          open=r["open"] or 0.0, high=r["high"] or 0.0,
                          low=r["low"] or 0.0, close=r["close"] or 0.0,
                          volume=int(r["volume"] or 0),
                          turn=r.get("turnover") or 0.0))
        if bl:
            out[c] = bl
    return out


def _submit_order(broker, risk, sig, bar, prev_close, prices,
                  cash, positions, total, trade_dt):
    """卖出按可用量 clamp → 风控 → 下单；被拦/不足返回 None。"""
    from src.paper.types import Order, OrderType, Signal
    vol = sig.volume
    if sig.direction.value == -1:
        pos = positions.get(sig.symbol)
        avail = pos.available_volume if pos else 0
        vol = min(vol, avail)
        if vol < 100:
            return None
        vol = vol // 100 * 100
    nsig = Signal(symbol=sig.symbol, direction=sig.direction, volume=vol,
                  price=sig.price, reason=sig.reason)
    ok, reason = risk.check_signal(nsig, bar.close, cash, positions,
                                   total, prices)
    if not ok:
        logger.info(f"[回测] 风控拦单 {sig.symbol}: {reason}")
        return None
    order = Order(symbol=sig.symbol, direction=sig.direction.value,
                  volume=vol, order_type=OrderType.LIMIT,
                  limit_price=bar.close, prev_close=prev_close,
                  ref_high=bar.high, ref_low=bar.low)
    order.created_at = trade_dt
    return broker.submit_order(order)


def _read_fills(dbfile):
    import sqlite3
    con = sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM fills ORDER BY fill_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _stats(fills):
    """FIFO 匹配：已实现盈亏 + 胜率（按卖出笔数）+ 总费用。"""
    lots = {}
    wins = sells = 0
    fee = realized = 0.0
    for f in fills:
        fee += (f["commission"] or 0) + (f["stamp_duty"] or 0) + (f["transfer_fee"] or 0)
        sym = f["symbol"]
        if f["direction"] == 1:
            unit = (f["price"] * f["volume"] + f["commission"] + f["transfer_fee"]) / f["volume"]
            lots.setdefault(sym, deque()).append([f["volume"], unit])
        else:
            need, cost = f["volume"], 0.0
            q = lots.get(sym, deque())
            while need > 0 and q:
                lv, lu = q[0]
                take = min(lv, need)
                cost += take * lu
                lv -= take
                need -= take
                if lv == 0:
                    q.popleft()
                else:
                    q[0][0] = lv
            gross = f["price"] * f["volume"]
            pnl = gross - (f["commission"] + f["stamp_duty"] + f["transfer_fee"]) - cost
            realized += pnl
            sells += 1
            if pnl > 0:
                wins += 1
    return (round(wins / sells, 3) if sells else None), fee, realized


def run_backtest(strategy, codes, start, end, initial_cash=100000.0,
                 params=None, progress_cb=None):
    """跑一次回测，返回 JSON 可序列化结果（含 summary/nav/trades）。"""
    from src.paper.broker import PaperBroker
    from src.paper.risk import RiskManager
    from src.paper.strategies import MACrossStrategy, plan_value_rotation
    from src.paper.types import TradingConfig
    params = params or {}
    if strategy not in ("ma", "value"):
        raise ValueError("strategy 非法：ma|value")
    codes = [c for c in dict.fromkeys(codes or []) if c]
    if not codes:
        raise ValueError("标的为空")
    if not (start and end and start <= end):
        raise ValueError("日期区间非法")
    bid = _next_id()
    tmp = tempfile.NamedTemporaryFile(prefix="paper_bt_", suffix=".db",
                                      delete=False)
    tmp.close()
    dbfile = tmp.name
    try:
        broker = PaperBroker(
            db_path=dbfile,
            config=TradingConfig(initial_cash=float(initial_cash or 100000)))
        risk = RiskManager()
        ma = MACrossStrategy(
            short_window=int(params.get("short_window", 5)),
            long_window=int(params.get("long_window", 20)),
            buy_volume=int(params.get("buy_volume", 100)),
            sell_volume=int(params.get("sell_volume", 100)))
        top_n = int(params.get("top_n", 10))
        dropout_n = int(params.get("dropout_n", 15))
        bars = load_bars(codes, start, end)
        if not bars:
            raise ValueError("区间无 K 线")
        dates = sorted({b.timestamp.date().isoformat()
                        for bl in bars.values() for b in bl})
        nav_series, peak, max_dd = [], float(initial_cash), 0.0
        last_month = None
        for i, d in enumerate(dates):
            trade_dt = datetime.fromisoformat(d)
            broker.set_trade_date(d)
            broker.unfreeze_t1(d)
            prefix = {c: [b for b in bl if b.timestamp.date().isoformat() <= d]
                      for c, bl in bars.items()}
            prefix = {c: bl for c, bl in prefix.items() if bl}
            closes = {c: bl[-1].close for c, bl in prefix.items()}
            if strategy == "ma":
                for c, bl in prefix.items():
                    if len(bl) < ma.long_window + 1:
                        continue
                    for sym, sig in ma.generate_signals({c: bl}).items():
                        prev = bl[-2].close if len(bl) >= 2 else None
                        _submit_order(
                            broker, risk, sig, bl[-1], prev, closes,
                            broker.get_cash(),
                            {p.symbol: p for p in broker.get_all_positions()},
                            broker.get_nav(closes).total_value, trade_dt)
            else:
                month = d[:7]
                if month != last_month:
                    last_month = month
                    scores = load_scores(d, max(top_n, dropout_n))
                    holdings = {p.symbol: p.total_volume
                                for p in broker.get_all_positions()}
                    cash = broker.get_cash()
                    total = broker.get_nav(closes).total_value
                    for sig in plan_value_rotation(
                            holdings, scores, closes, cash, total,
                            top_n, dropout_n):
                        _submit_order(
                            broker, risk, sig,
                            prefix[sig.symbol][-1],
                            (prefix[sig.symbol][-2].close
                             if len(prefix[sig.symbol]) >= 2 else None),
                            closes, broker.get_cash(),
                            {p.symbol: p for p in broker.get_all_positions()},
                            broker.get_nav(closes).total_value, trade_dt)
            nav = broker.get_nav(closes, timestamp=trade_dt)
            broker.record_nav(nav)
            peak = max(peak, nav.total_value)
            dd = (peak - nav.total_value) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
            nav_series.append({"date": d, "total": round(nav.total_value, 2),
                               "pnl_pct": round(nav.pnl_pct * 100, 2)})
            if progress_cb:
                progress_cb(i + 1, len(dates))
        fills = _read_fills(dbfile)
        win_rate, fee_total, realized = _stats(fills)
        result = {
            "backtest_id": bid, "strategy": strategy,
            "codes": sorted(bars), "start": start, "end": end,
            "summary": {
                "dates": len(dates), "trades": len(fills),
                "initial": float(initial_cash),
                "end_total": (round(nav_series[-1]["total"], 2)
                              if nav_series else float(initial_cash)),
                "return_pct": (round(nav_series[-1]["total"] / float(initial_cash) * 100 - 100, 2)
                               if nav_series else 0.0),
                "max_drawdown_pct": round(max_dd * 100, 2),
                "win_rate": win_rate, "fee_total": round(fee_total, 2),
                "realized": round(realized, 2)},
            "nav": nav_series,
            "trades": [{"date": (f.get("timestamp") or "")[:10],
                        "code": f["symbol"],
                        "direction": "买入" if f["direction"] == 1 else "卖出",
                        "volume": f["volume"], "price": f["price"],
                        "fee": round((f["commission"] or 0) + (f["stamp_duty"] or 0)
                                     + (f["transfer_fee"] or 0), 2)}
                       for f in fills]}
        _store(result)
        return result
    finally:
        try:
            os.unlink(dbfile)
        except OSError:
            pass
