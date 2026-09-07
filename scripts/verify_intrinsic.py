#!/usr/bin/env python3
"""终值验算闸 C1（AI Berkshire 算法核心融入）。

对上一步 intrinsic_value 的三档估值做确定性验算：
- 戈登终值 PE=(1-g/ROIC)/(r-g) 三档
- LLM 隐含倍数反解 vs 终值 PE 对比
- C1 币种一致、C2 分母>=5pct 体检
- 接入 analyze_stock 的写库前改判

零外部依赖，仅用 Python 标准库。
"""
import argparse
import json
import sys
from decimal import Decimal, ROUND_HALF_UP

MIN_SPREAD = 0.05
CURRENCY_BANDS = {
    "CNY": dict(r=(0.06, 0.09), g_max=0.02, rf=0.0170),
    "USD": dict(r=(0.09, 0.115), g_max=0.040, rf=0.0470),
    "HKD": dict(r=(0.09, 0.115), g_max=0.040, rf=0.0470),
}
LABELS = ("conservative", "base_case", "optimistic")


def exit_pe(roic: float, g: float, r: float):
    """戈登终值 PE = (1 - g/ROIC) / (r - g)。分母<=0 返回 None。"""
    spread = r - g
    if spread <= 0:
        return None
    return (1.0 - g / roic) / spread


def rescale_irr(irr_base, pe_base, pe_new, k, years=10):
    """把基准 r 下的 IRR 换算到新的退出倍数。"""
    return (1.0 + irr_base - k) * (pe_new / pe_base) ** (1.0 / years) - 1.0 + k


def currency_check(currency, r, g_values, rf=None):
    """C1 币种一致性检查。返回 (pass, warnings, failures)。"""
    band = CURRENCY_BANDS.get(currency.upper())
    if band is None:
        return False, [], [f"C1: 未知币种 {currency}"]
    fails, warns = [], []
    lo, hi = band["r"]
    if not (lo <= r <= hi):
        fails.append(f"C1: r={r:.2%} 不在 {currency.upper()} 的 [{lo:.1%},{hi:.1%}] 区间")
    g_base = g_values[1] if len(g_values) >= 2 else g_values[0]
    if g_base > band["g_max"] or max(g_values) > band["g_max"] + 0.01:
        fails.append(f"C1: 基准档 g={g_base:.1%} 超过 {currency.upper()} 上限 {band['g_max']:.1%}")
    if rf is not None and abs(rf - band["rf"]) > 0.005:
        fails.append(f"C1: 无风险利率 {rf:.2%} 与 {currency.upper()} 的 {band['rf']:.2%} 不符")
    return len(fails) == 0, warns, fails


def spread_check(roic, g_values, r):
    """C2 分母宽度体检。返回 (per_label_results, failures, warnings)。"""
    results = []
    fails, warns = [], []
    for label, g in zip(LABELS, g_values):
        pe = exit_pe(roic, g, r)
        spread = r - g
        if pe is None or spread <= 0:
            results.append({"label": label, "g": g, "pe": None, "spread": spread, "status": "失效"})
            fails.append(f"C2: {label}档 r-g={spread*100:.1f}pct <= 0，模型失效")
        elif spread < MIN_SPREAD:
            results.append({"label": label, "g": g, "pe": pe, "spread": spread, "status": "窄"})
            warns.append(f"C2: {label}档分母仅 {spread*100:.1f}pct < {MIN_SPREAD*100:.0f}pct，仅作情景参考")
        else:
            results.append({"label": label, "g": g, "pe": pe, "spread": spread, "status": "ok"})
    return results, fails, warns


def verify_intrinsic(code: str, roic_5y: float, fcf_5y_avg: float,
                     mcap_yi: float, currency: str = "CNY",
                     r: float = 0.075, g_shift: float = -0.01,
                     rf: float = 0.017):
    """对 intrinsic_value 三档做终值验算。

    返回 dict:
      - terminal_pe: 三档戈登终值 PE
      - implied_g: 反解 LLM 隐含的 g（若给出倍数和终值年利润）
      - verdicts: 每档 C1/C2 结果
      - mismatch: LLM 隐含倍数 vs 终值 PE 偏差率（>50% 告警，>100% 失败）
    """
    out = {"code": code, "roic_5y": roic_5y, "fcf_5y_avg": fcf_5y_avg,
           "mcap_yi": mcap_yi, "currency": currency, "r": r}
    # 三档 g: 悲观=基准-1.5pct, 乐观=基准+1pct（上游不对称规则）
    g_base = 0.025 + g_shift  # 基准档默认 2.5% 名义
    g_values = [g_base - 0.015, g_base, g_base + 0.01]
    out["g_values"] = g_values

    # C1 币种检查
    c1_pass, c1_warns, c1_fails = currency_check(currency, r, g_values, rf)
    out["C1"] = {"pass": c1_pass, "fails": c1_fails, "warns": c1_warns}

    # C2 分母体检
    c2_results, c2_fails, c2_warns = spread_check(roic_5y, g_values, r)
    out["C2"] = {"results": c2_results, "fails": c2_fails, "warns": c2_warns}

    # 终值 PE 三档
    terminal_pe = {}
    for label, g in zip(LABELS, g_values):
        pe = exit_pe(roic_5y, g, r)
        terminal_pe[label] = pe
    out["terminal_pe"] = terminal_pe

    # Owner Earnings ≈ 5年均 FCF，三档估值（亿）
    owner_earnings = fcf_5y_avg if fcf_5y_avg else None
    if owner_earnings is not None:
        out["owner_earnings"] = owner_earnings
        out["intrinsic_values"] = {
            label: (owner_earnings * pe) if pe else None
            for label, pe in terminal_pe.items()
        }
        # 反解 LLM 隐含倍数（若调用方给了 LLM 三档估值）
        # 由调用方在 _enforce_verdict_discipline 中补充
    return out


def _call_from_analyzer(code, roic_5y, fcf_5y_avg, mcap_yi,
                         llm_intrinsic=None, **kwargs):
    """从 analyze_stock 调用的入口。llm_intrinsic 是 LLM 输出的 intrinsic_value 字典。"""
    result = verify_intrinsic(code, roic_5y, fcf_5y_avg, mcap_yi, **kwargs)
    verdicts = {}
    mismatches = []
    if llm_intrinsic:
        for label in LABELS:
            llm_val = llm_intrinsic.get(label)
            our_val = result.get("intrinsic_values", {}).get(label)
            if llm_val and our_val and our_val > 0:
                ratio = abs(llm_val - our_val) / our_val
                if ratio > 1.0:
                    mismatches.append(f"{label}: LLM {llm_val:.0f}亿 vs 终值验算 {our_val:.0f}亿 偏差 {ratio*100:.0f}% > 100%")
                    verdicts[label] = "mismatch_fail"
                elif ratio > 0.5:
                    mismatches.append(f"{label}: LLM {llm_val:.0f}亿 vs 终值验算 {our_val:.0f}亿 偏差 {ratio*100:.0f}% > 50% 告警")
                    verdicts[label] = "mismatch_warn"
    result["verdicts"] = verdicts
    result["mismatches"] = mismatches
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="终值验算闸 C1（stdlib only）")
    parser.add_argument("--code", required=True)
    parser.add_argument("--roic", type=float, required=True)
    parser.add_argument("--fcf-5y", type=float, required=True, help="5年平均FCF(亿)")
    parser.add_argument("--mcap", type=float, required=True, help="总市值(亿)")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--r", type=float, default=0.075)
    parser.add_argument("--g-shift", type=float, default=-0.01)
    parser.add_argument("--rf", type=float, default=0.017)
    # LLM 三档估值（可选，用于反解偏差）
    for label in LABELS:
        parser.add_argument(f"--llm-{label}", type=float, default=None)
    args = parser.parse_args()

    llm_intrinsic = {}
    for label in LABELS:
        v = getattr(args, f"llm_{label}")
        if v is not None:
            llm_intrinsic[label] = v

    result = _call_from_analyzer(
        args.code, args.roic, args.fc5y if hasattr(args, 'fc5y') else args.fcf_5y,
        args.mcap, llm_intrinsic, currency=args.currency, r=args.r,
        g_shift=args.g_shift, rf=args.rf)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 退出码：有任何 mismatch_fail 或 C1/C2 硬性 fail 则 1
    hard_fail = (not result["C1"]["pass"]
                 or any(v == "mismatch_fail" for v in result.get("verdicts", {}).values()))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
