"""M4a Paper Trading Engine — 信号→委托→持仓→净值编排

职责：
- 解析 AI trade_strategy JSON → 生成买卖信号
- 通过 PaperBroker 下单 + 撮合
- 日终处理（T+1 解冻 + 净值记录）
- 独立模块，不污染 orchestrator / scheduler 的职责边界

调用方式：
- scheduler.py 在 AI 分析完成后调 run_paper_trading()
- 也可独立调用：python -m src.paper.engine
"""
import json
import logging
from datetime import datetime

from src.config import load_config
from src.paper.broker import PaperBroker
from src.utils import now_cn

logger = logging.getLogger(__name__)


def parse_trade_signal(stock: dict) -> dict | None:
    """从 screening_result 行解析 AI trade_strategy。

    返回：
        {"code": str, "signal": str, "confidence": str,
         "buy_zone": tuple|None, "target_price": float|None,
         "stop_loss": str, "take_profit": str} or None
    """
    code = stock.get("stock_code") or stock.get("code")
    trade_raw = stock.get("ai_trade_strategy")
    if not trade_raw:
        return None
    try:
        trade = json.loads(trade_raw) if isinstance(trade_raw, str) else trade_raw
    except (json.JSONDecodeError, TypeError):
        return None

    signal = str(trade.get("signal", "")).upper()
    if signal not in ("BUY", "SELL", "AVOID"):
        return None

    buy_zone = None
    bz = trade.get("buy_zone")
    if bz and isinstance(bz, str):
        parts = bz.replace("~", "-").split("-")
        try:
            nums = [float(p.strip()) for p in parts if p.strip()]
            if len(nums) == 2:
                buy_zone = (min(nums), max(nums))
            elif len(nums) == 1:
                buy_zone = (nums[0], nums[0])
        except (ValueError, TypeError):
            pass

    target_price = None
    tp = trade.get("target_price")
    if tp:
        try:
            target_price = float(str(tp).replace(",", "").replace("亿", ""))
        except (ValueError, TypeError):
            pass

    return {
        "code": code,
        "signal": signal,
        "confidence": str(trade.get("confidence", "中")),
        "buy_zone": buy_zone,
        "target_price": target_price,
        "stop_loss": str(trade.get("stop_loss", "")),
        "take_profit": str(trade.get("take_profit", "")),
    }


def _get_latest_close(code: str) -> float | None:
    """从 kline_daily 取最新收盘价"""
    from src.models.database import KlineDAO
    daily = KlineDAO().get_daily(code, limit=1)
    if daily and daily[-1].get("close"):
        return float(daily[-1]["close"])
    return None


def _get_current_price(code: str) -> float | None:
    """获取当前价格：优先实时行情，fallback kline 收盘价"""
    try:
        from src.scheduler import get_realtime_cache
        cache = get_realtime_cache()
        if code in cache and cache[code].get("current_price"):
            return float(cache[code]["current_price"])
    except Exception:
        pass
    return _get_latest_close(code)


def generate_signals(stocks: list[dict]) -> list[dict]:
    """从 AI 分析结果批量生成交易信号。

    Args:
        stocks: screening_result 行列表（含 ai_trade_strategy）

    Returns:
        信号列表
    """
    signals = []
    for s in stocks:
        sig = parse_trade_signal(s)
        if sig:
            sig["name"] = s.get("name", "")
            sig["score"] = s.get("score")
            signals.append(sig)
    return signals


def execute_signals(signals: list[dict], broker: PaperBroker = None,
                    config: dict = None) -> list[dict]:
    """执行交易信号：BUY → place_order + fill_order。

    Args:
        signals: generate_signals 的输出
        broker: PaperBroker 实例（可选，默认新建）
        config: 配置（可选）

    Returns:
        执行结果列表
    """
    if not signals:
        return []

    if broker is None:
        broker = PaperBroker()

    results = []
    for sig in signals:
        code = sig["code"]
        signal = sig["signal"]

        if signal == "BUY":
            result = _execute_buy(sig, broker)
        elif signal in ("SELL", "AVOID"):
            result = _execute_sell(sig, broker)
        else:
            result = {"code": code, "signal": signal, "action": "skip",
                      "reason": f"未知信号 {signal}"}

        result["name"] = sig.get("name", "")
        results.append(result)

    return results


def _execute_buy(sig: dict, broker: PaperBroker) -> dict:
    """执行买入信号"""
    code = sig["code"]
    current_price = _get_current_price(code)
    if current_price is None:
        return {"code": code, "signal": "BUY", "action": "skip",
                "reason": "无行情数据"}

    # 检查 buy_zone：有 buy_zone 且现价不在区间内 → 跳过
    buy_zone = sig.get("buy_zone")
    if buy_zone:
        low, high = buy_zone
        if not (low <= current_price <= high):
            return {"code": code, "signal": "BUY", "action": "skip",
                    "reason": f"现价 {current_price:.2f} 不在买入区 {buy_zone}"}

    # 计算仓位：单股 ≤ 20% 总资产，按 confidence 调整，预留 5% 滑点余量
    acc = broker.get_account()
    total_value = acc.get("total_value") or acc["cash"]
    max_pos_pct = broker._cfg.get("max_position_pct", 20) / 100

    confidence_mult = {"高": 1.0, "中": 0.6, "低": 0.3}
    mult = confidence_mult.get(sig.get("confidence", "中"), 0.6)

    # 预留滑点余量，避免风控边界刚好卡住
    slippage_pct = broker._cfg.get("slippage_pct", 0.1) / 100
    effective_max = max_pos_pct * (1 - slippage_pct * 2)
    target_value = total_value * effective_max * mult
    volume = int(target_value / current_price / 100) * 100  # 整手
    if volume <= 0:
        return {"code": code, "signal": "BUY", "action": "skip",
                "reason": "计算仓位为 0"}

    try:
        oid = broker.place_order(code, "BUY", current_price, volume,
                                 signal_source=sig.get("confidence"))
        fill = broker.fill_order(int(oid), current_price)
        return {"code": code, "signal": "BUY", "action": "filled",
                "order_id": oid, "volume": volume,
                "fill_price": fill["fill_price"],
                "total_cost": fill["total_cost"]}
    except ValueError as e:
        return {"code": code, "signal": "BUY", "action": "rejected",
                "reason": str(e)}


def _execute_sell(sig: dict, broker: PaperBroker) -> dict:
    """执行卖出信号（AVOID/SELL → 卖出可用持仓）"""
    code = sig["code"]
    pos = broker._positions.get(code)
    if not pos or pos["avail_volume"] <= 0:
        return {"code": code, "signal": sig["signal"], "action": "skip",
                "reason": "无可用持仓"}

    current_price = _get_current_price(code)
    if current_price is None:
        return {"code": code, "signal": sig["signal"], "action": "skip",
                "reason": "无行情数据"}

    try:
        oid = broker.place_order(code, "SELL", current_price,
                                 pos["avail_volume"])
        fill = broker.fill_order(int(oid), current_price)
        return {"code": code, "signal": sig["signal"], "action": "filled",
                "order_id": oid, "volume": pos["avail_volume"],
                "fill_price": fill["fill_price"],
                "total_cost": fill["total_cost"]}
    except ValueError as e:
        return {"code": code, "signal": sig["signal"], "action": "rejected",
                "reason": str(e)}


def run_paper_trading(stocks: list[dict] = None, config: dict = None) -> dict:
    """纸盘交易主入口。

    Args:
        stocks: screening_result 行列表（含 ai_trade_strategy），
                为 None 时自动从 DB 取最新一轮
        config: 配置（可选）

    Returns:
        {"signals": [...], "executions": [...], "nav": {...}}
    """
    if config is None:
        config = load_config()

    paper_cfg = config.get("paper", {})
    if not paper_cfg:
        logger.info("[纸盘] paper 配置为空，跳过")
        return {"signals": [], "executions": [], "nav": None}

    # 获取待处理的股票
    if stocks is None:
        from src.models.database import ScreeningResultDAO, StockAnalysisHistoryDAO
        try:
            latest_run = ScreeningResultDAO().get_latest_run_id()
            if not latest_run:
                logger.info("[纸盘] 无最新筛选结果，跳过")
                return {"signals": [], "executions": [], "nav": None}
            stocks = ScreeningResultDAO().get_results_for_run(latest_run)
            # 富集 AI trade_strategy
            hist_dao = StockAnalysisHistoryDAO()
            for s in stocks:
                if not s.get("ai_trade_strategy"):
                    hist = hist_dao.get_latest_for_code(s["stock_code"])
                    if hist and hist.get("ai_trade_strategy"):
                        s["ai_trade_strategy"] = hist["ai_trade_strategy"]
        except Exception as e:
            logger.warning(f"[纸盘] 获取筛选结果失败: {e}")
            return {"signals": [], "executions": [], "nav": None}

    # 1. 生成信号
    signals = generate_signals(stocks)
    logger.info(f"[纸盘] 生成 {len(signals)} 个信号："
                f"BUY={sum(1 for s in signals if s['signal']=='BUY')}, "
                f"SELL={sum(1 for s in signals if s['signal'] in ('SELL','AVOID'))}")

    # 2. 执行
    broker = PaperBroker()
    executions = execute_signals(signals, broker, config)
    filled = sum(1 for e in executions if e["action"] == "filled")
    logger.info(f"[纸盘] 执行 {filled}/{len(executions)} 笔成交")

    # 3. 日终处理
    date_str = now_cn().strftime("%Y-%m-%d")
    nav = broker.end_of_day(date_str)
    logger.info(f"[纸盘] 日终 NAV: {nav['total_value']:.2f} "
                f"(PnL: {nav['cumulative_pnl']:.2f})")

    return {"signals": signals, "executions": executions, "nav": nav}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_paper_trading()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
