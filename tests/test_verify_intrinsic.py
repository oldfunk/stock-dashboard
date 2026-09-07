#!/usr/bin/env python3
"""verify_intrinsic.py 单测（≥6 覆盖：戈登算式/三档不对称/C1 打回/C2 降级/反解偏差/空 FCF 诚实 SKIP）。"""

import json
import sys
from decimal import Decimal

sys.path.insert(0, '/home/pi/stock-dashboard')

from scripts.verify_intrinsic import (
    exact,
    deviation_pct,
    verdict_for,
    gordon_terminal_pe,
    implied_g_from_multiple,
    verify_intrinsic_for_stock,
)


def test_gordon_formula():
    """戈登终值 PE 公式正确性：PE = (1 - g/ROIC) / (r - g)"""
    # ROIC=15%, g=2%, r=7.5% (避开 C2 分母体检: r-g=5.5% >= 5%)
    roic = Decimal('0.15')
    g = Decimal('0.02')
    r = Decimal('0.075')
    # 分子 = 1 - 0.02/0.15 = 1 - 0.1333 = 0.8667
    # 分母 = 0.075 - 0.02 = 0.055
    # PE = 0.8667 / 0.055 = 15.757...
    res = gordon_terminal_pe(roic, g, r)
    assert res['denom_ok'] is True
    assert abs(res['pe'] - Decimal('15.75757575757575757575757576')) < Decimal('0.01')
    print("[通过] 戈登公式基准档计算正确")


def test_gordon_three_tiers_asymmetric():
    """三档不对称：悲观 g=0%、基准 g=3%、乐观 g=4%（非对称，非 ±1%）"""
    roic = Decimal('0.15')
    r = Decimal('0.075')

    pes = {}
    for tier, g in [('pessimistic', Decimal('0')),
                    ('base_case', Decimal('0.03')),
                    ('optimistic', Decimal('0.04'))]:
        res = gordon_terminal_pe(roic, g, r)
        # 基准和乐观档会触发 C2 分母体检，这里只验证公式计算正确性
        pes[tier] = res['pe']

    # 悲观 < 基准 < 乐观（g 越大 PE 越大），但基准/乐观分母失效返回 None
    assert pes['pessimistic'] is not None
    # 基准档 g=3%: r-g=4.5% < 5% → 分母失效 → pe=None
    assert pes['base_case'] is None
    # 乐观档 g=4%: r-g=3.5% < 5% → 分母失效 → pe=None
    assert pes['optimistic'] is None
    # 悲观 g=0% 时 PE = 1/(r) = 1/0.075 = 13.33...
    assert abs(pes['pessimistic'] - Decimal('13.33333333333333333333333333')) < Decimal('0.01')
    print("[通过] 三档不对称 g 值与 C2 分母体检正确")


def test_c2_denom_floor():
    """C2 分母体检：r - g < 5pct → 分母失效"""
    roic = Decimal('0.15')
    r = Decimal('0.075')

    # g=3%: r-g = 4.5% < 5% → 失效
    res = gordon_terminal_pe(roic, Decimal('0.03'), r)
    assert res['denom_ok'] is False
    assert '分母失效' in res['note']

    # g=2%: r-g = 5.5% >= 5% → 正常
    res = gordon_terminal_pe(roic, Decimal('0.02'), r)
    assert res['denom_ok'] is True

    # g=2.5%: r-g = 5.0% >= 5% → 正常（边界）
    res = gordon_terminal_pe(roic, Decimal('0.025'), r)
    assert res['denom_ok'] is True

    print("[通过] C2 分母体检 5pct 下限正确")


def test_c1_implied_g_cap():
    """C1 体检：隐含 g > 2% → 打回"""
    roic = Decimal('0.15')
    r = Decimal('0.075')

    # 隐含 g 反解测试（修正公式后）
    # g = (multiple * r - 1) / (multiple - 1/ROIC)
    # ROIC=15% 时 1/ROIC = 6.67
    # multiple=30: g = (2.25-1)/(30-6.67) = 1.25/23.33 = 5.36% > 2% → 触发 C1
    g = implied_g_from_multiple(Decimal('30'), roic, r)
    assert g is not None
    assert g > Decimal('0.02')  # 现在正确触发 C1

    # 低 ROIC 更容易触发 C1
    roic_low = Decimal('0.05')  # 5% ROIC，1/ROIC = 20
    # multiple=25: g = (1.875-1)/(25-20) = 0.875/5 = 17.5% > 2%
    g = implied_g_from_multiple(Decimal('25'), roic_low, r)
    assert g is not None
    assert g > Decimal('0.02')

    # 高 ROIC + 低倍数可能不触发 C1
    roic_high = Decimal('0.30')  # 30% ROIC，1/ROIC = 3.33
    # multiple=15: g = (1.125-1)/(15-3.33) = 0.125/11.67 = 1.07% < 2%
    g = implied_g_from_multiple(Decimal('15'), roic_high, r)
    assert g is not None
    assert g < Decimal('0.02')
    print(f"[通过] C1 隐含 g 反解正确: ROIC=15% multiple=30 隐含g={float(g)*100:.2f}% (触发C1), ROIC=30% multiple=15 隐含g={float(g)*100:.2f}% (未触发)")


def test_deviation_verdict():
    """偏差判定：>50% 告警、>100% 或符号矛盾失败"""
    # 正常偏差
    assert verdict_for(30) == 'PASS'
    assert verdict_for(60) == 'WARN'
    assert verdict_for(120) == 'FAIL'
    assert verdict_for(None) == 'SKIP'

    # 符号矛盾在 verify_intrinsic_for_stock 中单独处理
    print("[通过] 偏差判定阈值正确")


def test_negative_fcf_skip():
    """空/负 FCF 诚实 SKIP：Owner Earnings 不可用时全档 SKIP"""
    stock_data = {
        'code': '000001',
        'name': '测试股',
        'roic_5y_avg': 15.0,
        'fcf_5y_sum': -100000000,  # 负 FCF
        'market_cap': 100,
        'current_price': 50,
        'intrinsic_value': {
            'conservative': '80',
            'base_case': '100',
            'optimistic': '120',
        },
    }
    res = verify_intrinsic_for_stock(stock_data)
    for tier in ['pessimistic', 'base_case', 'optimistic']:
        assert res['implied'][tier]['verdict'] == 'SKIP'
        assert 'Owner Earnings 不可用' in res['implied'][tier]['note'] or \
               '缺 LLM 估值或 Owner Earnings 不可用' in res['implied'][tier]['note']
    print("[通过] 负 FCF 时诚实 SKIP 所有档")


def test_missing_intrinsic_skip():
    """缺 intrinsic_value 时诚实 SKIP"""
    stock_data = {
        'code': '000001',
        'name': '测试股',
        'roic_5y_avg': 15.0,
        'fcf_5y_sum': 5000000000,  # 50亿
        'market_cap': 100,
        'current_price': 50,
        'intrinsic_value': {},  # 空
    }
    res = verify_intrinsic_for_stock(stock_data)
    for tier in ['pessimistic', 'base_case', 'optimistic']:
        assert res['implied'][tier]['verdict'] == 'SKIP'
    print("[通过] 缺 intrinsic_value 时诚实 SKIP")


def test_implied_g_calculation():
    """隐含 g 反解数学正确性验证"""
    roic = Decimal('0.20')  # 20%
    r = Decimal('0.075')

    # 使用 g=2% 避开 C2 分母体检 (r-g=5.5% >= 5%)
    terminal = gordon_terminal_pe(roic, Decimal('0.02'), r)
    pe = terminal['pe']
    assert pe is not None
    # 反解应得回 g≈2%
    g_back = implied_g_from_multiple(pe, roic, r)
    assert g_back is not None
    assert abs(g_back - Decimal('0.02')) < Decimal('0.001')
    print(f"[通过] 隐含 g 反解自洽: 正向 PE={float(pe):.2f} → 反解 g={float(g_back)*100:.2f}%")


def test_unit_conversion():
    """单位换算陷阱：百分比/小数、元/亿"""
    # roic_5y_avg 输入是 %（如 15.5），内部转小数 0.155
    # fcf_5y_sum 输入是元，内部转亿并除以 5 得年均亿
    # market_cap 输入是亿
    # current_price 输入是元
    # 使用更高的 ROIC 和更低的 g 避开 C2
    stock_data = {
        'code': '000001',
        'name': '测试股',
        'roic_5y_avg': '30.0',  # 字符串 % (高 ROIC)
        'fcf_5y_sum': '5000000000',  # 字符串 元 (50亿)
        'market_cap': '100',  # 字符串 亿
        'current_price': '50',  # 字符串 元
        'intrinsic_value': {
            'conservative': '80',
            'base_case': '100',
            'optimistic': '120',
        },
    }
    res = verify_intrinsic_for_stock(stock_data)
    # ROIC 应正确转为 0.30
    assert res['roic_5y_avg_pct'] == 30.0
    # 年均 FCF 应为 50亿/5 = 10亿
    assert res['fcf_annual_yi'] == 10.0
    # Owner Earnings = 10亿
    # LLM 倍数 = 100/10 = 10倍 (base_case)
    # 终值 PE (g=3%, ROIC=30%, r=7.5%) = (1-0.03/0.30)/(0.075-0.03) = 0.9/0.045 = 20
    # 但 g=3% 触发 C2 (r-g=4.5%<5%)，所以 terminal_pe 为 None
    # 悲观档 g=0%: PE = 1/0.075 = 13.33
    assert res['implied']['base_case']['implied_multiple'] == 10.0
    assert res['terminal']['base_case']['terminal_pe'] is None  # C2 触发
    assert res['terminal']['pessimistic']['terminal_pe'] is not None
    assert abs(res['terminal']['pessimistic']['terminal_pe'] - 13.33) < 0.1
    # 偏差：base_case terminal_pe 为 None → SKIP
    assert res['implied']['base_case']['verdict'] == 'SKIP'
    print(f"[通过] 单位换算正确: ROIC%→小数, 元→亿, 年均FCF, C2触发导致base_case SKIP")


def run_all():
    test_gordon_formula()
    test_gordon_three_tiers_asymmetric()
    test_c2_denom_floor()
    test_c1_implied_g_cap()
    test_deviation_verdict()
    test_negative_fcf_skip()
    test_missing_intrinsic_skip()
    test_implied_g_calculation()
    test_unit_conversion()
    print("\n=== 所有单测通过 (9/9) ===")


if __name__ == '__main__':
    run_all()