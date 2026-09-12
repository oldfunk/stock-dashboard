"""新股票自动填充数据管道。

任何新股票进入系统时调用 onboard_stock(code)，自动完成：
1. stock_snapshot（实时行情 + 财务指标）
2. financial_history（历史年报：ROE、毛利率、FCF 等）
3. financial_summary（5年/10年均值）

设计原则：
- 幂等：重复调用不会产生副作用（DAO 都是 UPSERT）
- 降级：任何步骤失败不影响后续步骤，返回 status dict
- 可组合：供钉选入口、详情页兜底、批量导入等场景复用
"""

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


def onboard_stock(code: str, trigger_ai: bool = False) -> dict:
    """为单只股票填充全量数据。

    Args:
        code: 6位股票代码
        trigger_ai: 是否触发 AI 分析（默认 False，避免阻塞）

    Returns:
        {"ok": bool, "code": str, "steps": {...}, "errors": [...]}
    """
    result = {"ok": True, "code": code, "steps": {}, "errors": []}

    # Step 1: stock_snapshot + 基础财务指标
    try:
        _step_snapshot(code, result)
    except Exception as e:
        result["errors"].append(f"snapshot: {e}")
        logger.warning(f"[Onboard] {code} snapshot 失败: {e}")

    # Step 2: financial_history（历史年报）
    try:
        _step_financial_history(code, result)
    except Exception as e:
        result["errors"].append(f"financial_history: {e}")
        logger.warning(f"[Onboard] {code} financial_history 失败: {e}")

    # Step 3: financial_summary（5年/10年均值）
    try:
        _step_financial_summary(code, result)
    except Exception as e:
        result["errors"].append(f"financial_summary: {e}")
        logger.warning(f"[Onboard] {code} financial_summary 失败: {e}")

    result["ok"] = len(result["errors"]) == 0
    return result


def onboard_stock_async(code: str, trigger_ai: bool = False):
    """异步版本：在后台线程中执行 onboard_stock，不阻塞调用方。"""
    def _run():
        try:
            onboard_stock(code, trigger_ai=trigger_ai)
        except Exception as e:
            logger.warning(f"[Onboard] {code} 异步执行失败: {e}")
    threading.Thread(target=_run, daemon=True).start()


def _step_snapshot(code: str, result: dict):
    """Step 1: 拉取实时行情 + 财务指标，写入 stock_snapshot。"""
    from src.collector.akshare_fetcher import fetch_tencent_batch, enrich_financial_data
    from src.models.database import StockSnapshotDAO

    quotes = fetch_tencent_batch([code])
    if not quotes:
        result["steps"]["snapshot"] = "no_quote"
        return

    enrich_financial_data(quotes)
    today = datetime.now().strftime("%Y-%m-%d")
    for q in quotes:
        q["snapshot_date"] = q.get("snapshot_date") or today
    StockSnapshotDAO().save_batch(quotes)
    result["steps"]["snapshot"] = "ok"


def _step_financial_history(code: str, result: dict):
    """Step 2: 拉取历史年报，写入 financial_history。"""
    from src.collector.akshare_fetcher import collect_historical_financial_data

    stocks = [{"code": code}]
    stats = collect_historical_financial_data(stocks, force_full=True)
    result["steps"]["financial_history"] = "ok" if stats.get(code) else "no_data"


def _step_financial_summary(code: str, result: dict):
    """Step 3: 从 financial_history 重建 financial_summary。"""
    from src.collector.akshare_fetcher import rebuild_financial_summaries
    from src.models.database import FinancialSummaryDAO

    rebuild_financial_summaries([{"code": code}])
    summary = FinancialSummaryDAO().get(code)
    result["steps"]["financial_summary"] = "ok" if summary else "no_data"
