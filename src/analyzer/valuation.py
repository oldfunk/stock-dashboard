"""可复现估值模型（2026-10-23 P0-6，价值投资方法论核心）。

取代"LLM 主观给三档估值"：用显式假设的 **FCFF 两阶段折现** 算出可复现的
内在价值与程序化安全边际。同一假设 → 同一结果（可审计 / 可回测）。

- 自由现金流取 ``financial_history.fcf``（东财 FCFF_BACK，年报口径）。
- 净负债 = 有息负债 − 货币资金（P0-3 数据）。
- 三档 = 增长率/折现率假设不同（保守/基准/乐观），不是倍数不同。
- 数据不足（缺 fcf / shares）→ 返回 None，如实标注，不硬算。
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
    """股权价值 = 企业价值 − 净负债；每股 = 股权价值 / 总股本。"""
    if firm_value is None or shares is None or shares <= 0:
        return None
    nd = net_debt or 0.0
    return (firm_value - nd) / shares


def net_debt(financials: dict) -> float | None:
    ibd = financials.get("interest_bearing_debt")
    cash = financials.get("cash")
    if ibd is None and cash is None:
        return None
    return (ibd or 0.0) - (cash or 0.0)


def intrinsic_value(financials: dict, price: float | None) -> dict:
    """返回 {intrinsic_value:{三档}, margin_of_safety, method, net_debt}。

    缺 fcf / shares 时对应档为 None（数据不足，不硬算）。
    """
    fcf = financials.get("fcf")
    shares = financials.get("total_shares")
    nd = net_debt(financials)
    out: dict[str, float | None] = {}
    for name, (y1, g1, g2, gt, r) in _SCENARIOS.items():
        fv = dcf_value(fcf, y1, g1, g2, gt, r)
        out[name] = equity_per_share(fv, nd, shares)
    base = out.get("base_case")
    mos = None
    if base and price and base > 0:
        mos = round((1 - price / base) * 100, 1)
    return {
        "intrinsic_value": out,
        "margin_of_safety": mos,
        "net_debt": nd,
        "method": "FCFF 两阶段折现（Gordon 终值）",
        "sufficient": fcf is not None and shares is not None,
    }
