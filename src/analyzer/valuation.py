"""可复现估值模型（2026-10-23 P0-6，价值投资方法论核心）。

取代"LLM 主观给三档估值"：用显式假设的 **FCFF 两阶段折现** 算出可复现的
内在价值与程序化安全边际。同一假设 → 同一结果（可审计 / 可回测）。

- 自由现金流取 ``financial_history.fcf``（东财 FCFF_BACK，年报口径）。
- 净负债 = 有息负债 − 货币资金（P0-3 数据）。
- 三档 = 增长率/折现率假设不同（保守/基准/乐观），不是倍数不同。
- **fail-closed（2026-10-25）**：缺 fcf / 总股本 / 货币资金 / 有息负债任一项，
  或 fcf <= 0，都不出估值，``sufficient=False`` 并给出具体原因。
  历史教训：旧实现只查 fcf + shares，201 只 fcf<=0 的股票 ``sufficient=True``
  却三档全空；29 只净负债未知被当成 0，600389 基准估值被高估到 3.72（应为 0.21）。
"""

import logging

logger = logging.getLogger(__name__)

# 两阶段 DCF 假设：(第一阶段年数, 第一阶段增长, 第二阶段增长, 终值增长, 折现率)
_SCENARIOS = {
    "conservative": (5, 0.00, 0.00, 0.00, 0.10),
    "base_case": (5, 0.03, 0.02, 0.02, 0.09),
    "optimistic": (5, 0.06, 0.04, 0.03, 0.08),
}


def dcf_value(fcf: float, years1: int, g1: float, g2: float,
              g_term: float, r: float) -> float | None:
    """两阶段 FCFF 折现。缺参数或 r <= g_term 返回 None。"""
    if fcf is None or fcf <= 0 or r is None or r <= g_term:
        return None
    value = 0.0
    cf = fcf
    for _ in range(years1):
        cf *= (1 + g1)
        value += cf / (1 + r) ** (_ + 1)
    for t in range(years1 + 1, years1 + 5 + 1):
        cf *= (1 + g2)
        value += cf / (1 + r) ** t
    terminal = cf * (1 + g_term) / (r - g_term)
    value += terminal / (1 + r) ** (years1 + 5)
    return value


def equity_per_share(firm_value: float | None, net_debt: float | None,
                     shares: float | None) -> float | None:
    """股权价值 = 企业价值 − 净负债；每股 = 股权价值 / 总股本。

    ``net_debt`` 未知（None）→ None：未知净负债不能当 0 处理（会把估值算高）。
    """
    if firm_value is None or shares is None or shares <= 0 or net_debt is None:
        return None
    return (firm_value - net_debt) / shares


def net_debt(financials: dict) -> float | None:
    """净负债 = 有息负债 − 货币资金；任一侧缺失返回 None（未知 ≠ 0）。"""
    ibd = financials.get("interest_bearing_debt")
    cash = financials.get("cash")
    if ibd is None or cash is None:
        return None
    return ibd - cash


# 估值不可用原因（供 UI / AI prompt 如实说明，不显示占位数字）
_REASON_TEXT = {
    "missing_fcf": "最新年报缺自由现金流（FCF）",
    "negative_fcf": "最新年报自由现金流为负（FCF ≤ 0），两阶段 DCF 不适用",
    "missing_shares": "最新年报缺总股本，无法换算每股价值",
    "missing_net_debt": "最新年报缺货币资金或有息负债，净负债无法确定",
    "not_discountable": "折现假设不成立（折现率不高于永续增长）",
}


def _insufficient_reason(fcf, shares, nd) -> str | None:
    """按优先级判断估值输入是否完整；完整返回 None。"""
    if fcf is None:
        return "missing_fcf"
    if fcf <= 0:
        return "negative_fcf"
    if shares is None or shares <= 0:
        return "missing_shares"
    if nd is None:
        return "missing_net_debt"
    return None


def intrinsic_value(financials: dict, price: float | None) -> dict:
    """返回 {intrinsic_value:{三档}, margin_of_safety, method, net_debt, sufficient, ...}。

    **fail-closed**：任一估值输入缺失或 FCF 非正 → 三档全 None、
    ``sufficient=False``，并在 ``insufficient_reason`` 给出机器可读原因、
    ``insufficient_text`` 给出中文说明。不硬算、不用占位值。
    """
    fcf = financials.get("fcf")
    shares = financials.get("total_shares")
    nd = net_debt(financials)
    reason = _insufficient_reason(fcf, shares, nd)
    out: dict[str, float | None] = {}
    for name, (y1, g1, g2, gt, r) in _SCENARIOS.items():
        if reason is not None:
            out[name] = None
            continue
        out[name] = equity_per_share(
            dcf_value(fcf, y1, g1, g2, gt, r), nd, shares)
    base = out.get("base_case")
    if reason is None and base is None:
        reason = "not_discountable"  # 折现率 <= 永续增长等假设失效
    mos = None
    if reason is None and base and price and base > 0:
        mos = round((1 - price / base) * 100, 1)
    return {
        "intrinsic_value": out,
        "margin_of_safety": mos,
        "net_debt": nd,
        "method": "FCFF 两阶段折现（Gordon 终值）",
        "sufficient": reason is None and base is not None,
        "insufficient_reason": reason,
        "insufficient_text": _REASON_TEXT.get(reason) if reason else None,
        "inputs": {
            "fcf": fcf,
            "total_shares": shares,
            "cash": financials.get("cash"),
            "interest_bearing_debt": financials.get("interest_bearing_debt"),
        },
    }


def scenario_assumptions() -> dict:
    """三档假设（供 UI 展示：同一模型 → 同一结果，可审计）。"""
    return {
        name: {"years1": y1, "g1": g1, "g2": g2, "g_term": gt, "discount": r}
        for name, (y1, g1, g2, gt, r) in _SCENARIOS.items()
    }


def load_financials(code: str) -> dict | None:
    """取某股最新年报的估值输入（fcf/total_shares/cash/interest_bearing_debt）。

    只读 ``financial_history`` 年报；无数据返回 None（调用方 fail-closed）。
    """
    if not code:
        return None
    from src.models.database import FinancialHistoryDAO
    try:
        rows = FinancialHistoryDAO().get_annual_reports(code)
    except Exception:  # noqa: BLE001 - 取数失败按“无数据”处理，不阻断页面
        return None
    if not rows:
        return None
    r = rows[0]
    return {
        "fcf": r.get("fcf"),
        "total_shares": r.get("total_shares"),
        "cash": r.get("cash"),
        "interest_bearing_debt": r.get("interest_bearing_debt"),
        "report_date": r.get("report_date"),
    }


def intrinsic_value_for_code(code: str, price: float | None = None) -> dict | None:
    """按股票代码算模型估值（统一入口：Web 展示 + AI prompt 共用）。

    返回 ``intrinsic_value()`` 结果 + ``price``/``report_date``/``assumptions``；
    无年报数据返回 None。价格缺省时从快照读当前价。
    """
    fin = load_financials(code)
    if fin is None:
        return None
    if price is None:
        from src.models.database import StockSnapshotDAO
        try:
            snap = StockSnapshotDAO().get_by_code(code)
            price = snap.get("current_price") if snap else None
        except Exception:  # noqa: BLE001
            price = None
    out = intrinsic_value(fin, price)
    out["price"] = price
    out["report_date"] = fin.get("report_date")
    out["assumptions"] = scenario_assumptions()
    return out
