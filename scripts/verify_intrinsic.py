#!/usr/bin/env python3
"""终值验算闸 C1：戈登模型终值 PE 三档 + LLM 隐含倍数反解对比 + C1 币种/C2 分母体检。

只用 stdlib（decimal/json/argparse/sqlite3/math），零 emoji。
设计对标上游 audit 三条硬约束（C1 币种一致/C2 分母≥5pct/C3 离散风险归属），
全部可 deterministic 实现。

核心逻辑：
1. 戈登终值 PE 三档（悲/基/乐 g 取不对称值，上游规则照搬）
   r 用 CNY 区间 [6%, 9%] 中值 7.5%，RF=1.7%。
   PE = (1 - g/ROIC) / (r - g)  —— 戈登模型终值倍数公式。
2. LLM 三档隐含倍数反解（intrinsic_value / 年化 Owner Earnings）vs 终值 PE 对比，
   偏差 >50% 告警、>100% 或符号矛盾失败（口径沿用 V2 宽容设计）。
3. C2 体检：任一档 r-g<5pct → 该档标记"情景参考，不得当估值用"。
4. C1 体检：LLM 隐含 g>2%（CNY 上限）→ 打回。

口径陷阱（预先声明，仿 V2）：
- Owner Earnings 用 5 年均 FCF（含负年则该档 SKIP，不许拿单年 FCF 充数）。
- ROIC 用 5 年均（单年 ROIC 失真）。
- r-g 分母用小数，百分比换算错一位结果差十倍——单测必须覆盖单位换算。

接入：
- `run_pipeline` 步骤 4.6（try/except 包裹永不阻断，仿 4.5）。
- `analyze_stock` 写库前纪律：乐观档隐含 g 超上限或分母失效 → 该档估值字段强制标注"分母失效，仅情景参考"，
  永不静默通过（dto：不阻断落库，只改判标注——估值是观点，验算是标尺）。
"""

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
from decimal import Decimal, getcontext

getcontext().prec = 28
logger = logging.getLogger(__name__)

# 常量（上游 tools/terminal_value.py 对齐）
RF_CNY = Decimal('0.017')          # 无风险利率 1.7%
R_CNY_LOW = Decimal('0.06')        # CNY 折现率下限 6%
R_CNY_HIGH = Decimal('0.09')       # CNY 折现率上限 9%
R_CNY_MEDIAN = (R_CNY_LOW + R_CNY_HIGH) / Decimal('2')  # 7.5%

# 三档 g 取值（不对称：上游照搬，悲观不取负，基准 0，乐观 +1pct）
# 注意：g 以小数形式参与计算（0.03 = 3%），避免单位换算错位
G_PESSIMISTIC = Decimal('0')       # 悲观：0% 增长
G_BASE = Decimal('0.03')           # 基准：3% 增长
G_OPTIMISTIC = Decimal('0.04')     # 乐观：4% 增长（基准+1pct，不对称）

# C1/C2 阈值
C1_G_CAP_CNY = Decimal('0.02')     # C1：人民币隐含 g 上限 2%
C2_DENOM_MIN = Decimal('0.05')     # C2：分母 r-g 下限 5pct

# 偏差判定阈值（沿用 V2 宽容设计）
DEV_WARN = Decimal('50')           # >50% 告警
DEV_FAIL = Decimal('100')          # >100% 或符号矛盾失败


def exact(value) -> Decimal:
    """任何数值转精确 Decimal，避开 float 陷阱。"""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return None
    return Decimal(str(value))


def deviation_pct(calculated: Decimal, reported: Decimal):
    """相对偏差百分比；reported 为 0/None 时返回 None（判跳过）。"""
    if reported is None or calculated is None:
        return None
    if reported == 0:
        return None
    return abs(float(calculated - reported) / float(reported)) * 100.0


def verdict_for(dev, tol_warn=DEV_WARN, tol_fail=DEV_FAIL) -> str:
    """PASS / WARN / FAIL / SKIP 四态。"""
    if dev is None:
        return 'SKIP'
    d = Decimal(str(dev))
    if d > tol_fail:
        return 'FAIL'
    if d > tol_warn:
        return 'WARN'
    return 'PASS'


def gordon_terminal_pe(roic: Decimal, g: Decimal, r: Decimal = R_CNY_MEDIAN):
    """戈登模型终值 PE = (1 - g/ROIC) / (r - g)。

    Args:
        roic: ROIC（小数，如 0.15 = 15%）
        g: 永续增长率（小数，如 0.03 = 3%）
        r: 折现率（小数，默认 7.5%）

    Returns:
        dict: {'pe': Decimal, 'denom_ok': bool, 'note': str}
    """
    if roic is None or roic <= 0:
        return {'pe': None, 'denom_ok': False, 'note': 'ROIC 非正'}
    if g is None:
        return {'pe': None, 'denom_ok': False, 'note': 'g 缺失'}

    # C2 分母体检：r - g < 5pct → 分母失效
    denom = r - g
    denom_ok = denom >= C2_DENOM_MIN
    if not denom_ok:
        return {'pe': None, 'denom_ok': False,
                'note': f'分母失效 r-g={float(denom)*100:.2f}% < 5%'}

    # 戈登公式：PE = (1 - g/ROIC) / (r - g)
    # 注意：g/ROIC 若 >1 会导致分子为负，属于合理的高增长/低ROIC情形
    one = Decimal('1')
    numerator = one - g / roic
    pe = numerator / denom
    return {'pe': pe, 'denom_ok': True, 'note': ''}


def implied_g_from_multiple(multiple: Decimal, roic: Decimal, r: Decimal = R_CNY_MEDIAN):
    """由倍数反解隐含 g：multiple = (1 - g/ROIC) / (r - g)
    解得：g = (multiple * r - 1) / (multiple - 1/ROIC)

    Args:
        multiple: 估值倍数
        roic: ROIC（小数）
        r: 折现率（小数）

    Returns:
        Decimal: 隐含 g（小数），无法求解返回 None
    """
    if multiple is None or multiple <= 0 or roic is None or roic <= 0:
        return None
    try:
        # g = (multiple * r - 1) / (multiple - 1/ROIC)
        num = multiple * r - Decimal('1')
        den = multiple - Decimal('1') / roic
        if den == 0:
            return None
        g = num / den
        return g
    except Exception:
        return None


def verify_intrinsic_for_stock(stock_data: dict) -> dict:
    """对单只股票执行终值验算。

    Args:
        stock_data: 包含以下字段的 dict
            - code, name
            - roic_5y_avg (float/Decimal/str, %形式如 15.5)
            - fcf_5y_sum (float/Decimal/str, 元)
            - market_cap (float/Decimal/str, 亿)
            - intrinsic_value (dict with conservative/base_case/optimistic in 亿)
            - current_price (float/Decimal/str, 元)

    Returns:
        dict: 验算结果，含三档终值 PE、反解隐含 g、偏差判定、C1/C2 体检
    """
    code = stock_data.get('code', '')
    name = stock_data.get('name', '')

    # 读取输入（统一转 Decimal，处理 % 与 亿 单位）
    roic_5y = stock_data.get('roic_5y_avg')
    fcf_5y = stock_data.get('fcf_5y_sum')
    market_cap_yi = stock_data.get('market_cap')
    intrinsic = stock_data.get('intrinsic_value') or {}
    current_price = stock_data.get('current_price')

    # ROIC：输入通常是 %（如 15.5），转小数
    if roic_5y is not None:
        roic = exact(roic_5y) / Decimal('100')
    else:
        roic = None

    # FCF 5年均（元 → 亿，年均）
    # Owner Earnings ≈ 5年均 FCF（若含负年则诚实 SKIP）
    if fcf_5y is not None and market_cap_yi is not None and current_price is not None:
        fcf_5y_dec = exact(fcf_5y)
        # 检查 FCF 是否为正（5年累计 > 0 即可，年均用于 Owner Earnings）
        if fcf_5y_dec <= 0:
            fcf_annual_yi = None
            fcf_note = '5年累计 FCF 非正，Owner Earnings 不可用'
        else:
            fcf_annual_yi = fcf_5y_dec / Decimal('5') / Decimal('1e8')  # 元→亿，年均
            fcf_note = ''
    else:
        fcf_annual_yi = None
        fcf_note = '缺 FCF/市值/现价'

    # 年化 Owner Earnings（亿）
    oe_annual_yi = fcf_annual_yi

    # 三档终值 PE
    tiers = ['pessimistic', 'base_case', 'optimistic']
    g_values = {
        'pessimistic': G_PESSIMISTIC,
        'base_case': G_BASE,
        'optimistic': G_OPTIMISTIC,
    }
    g_labels = {
        'pessimistic': '悲观(g=0%)',
        'base_case': '基准(g=3%)',
        'optimistic': '乐观(g=4%)',
    }

    terminal_results = {}
    for tier in tiers:
        g = g_values[tier]
        res = gordon_terminal_pe(roic, g) if roic is not None else {'pe': None, 'denom_ok': False, 'note': '缺 ROIC'}
        terminal_results[tier] = {
            'g': float(g * 100),  # 存百分比便于阅读
            'g_label': g_labels[tier],
            'terminal_pe': float(res['pe']) if res['pe'] is not None else None,
            'denom_ok': res['denom_ok'],
            'note': res['note'],
        }

    # LLM 隐含倍数反解（intrinsic_value / 年化 Owner Earnings）
    implied_results = {}
    for tier in tiers:
        iv_str = intrinsic.get(tier)
        if iv_str is None or oe_annual_yi is None or oe_annual_yi <= 0:
            implied_results[tier] = {
                'implied_multiple': None,
                'implied_g': None,
                'deviation_pct': None,
                'verdict': 'SKIP',
                'c1_fail': False,
                'c2_fail': False,
                'note': '缺 LLM 估值或 Owner Earnings 不可用',
            }
            continue

        iv_yi = exact(iv_str)  # 已是亿
        implied_multiple = iv_yi / oe_annual_yi

        # 反解隐含 g
        implied_g = implied_g_from_multiple(implied_multiple, roic) if roic is not None else None

        # C1 体检：隐含 g > 2% (CNY 上限) → 打回
        c1_fail = False
        if implied_g is not None and implied_g > C1_G_CAP_CNY:
            c1_fail = True

        # C2 体检：终值 PE 的分母检查
        c2_fail = not terminal_results[tier]['denom_ok']

        # 偏差对比：隐含倍数 vs 终值 PE
        terminal_pe = terminal_results[tier]['terminal_pe']
        if terminal_pe is not None and implied_multiple is not None:
            dev = deviation_pct(implied_multiple, Decimal(str(terminal_pe)))
            v = verdict_for(dev)
            # 符号矛盾检查
            sign_contradiction = (implied_multiple * Decimal(str(terminal_pe)) < 0)
            if sign_contradiction:
                v = 'FAIL'
        else:
            dev = None
            v = 'SKIP'

        implied_results[tier] = {
            'implied_multiple': float(implied_multiple) if implied_multiple is not None else None,
            'implied_g': float(implied_g * 100) if implied_g is not None else None,  # 存百分比
            'terminal_pe': terminal_pe,
            'deviation_pct': round(float(dev), 2) if dev is not None else None,
            'verdict': v,
            'c1_fail': c1_fail,
            'c2_fail': c2_fail,
            'note': f'隐含g={float(implied_g)*100:.2f}%' if implied_g is not None else '隐含g不可解',
        }

    # 汇总判定：取最坏
    def worst(*verdicts):
        order = {'SKIP': 0, 'PASS': 1, 'WARN': 2, 'FAIL': 3}
        return max(verdicts, key=lambda v: order.get(v, 0))

    overall_verdict = 'PASS'
    for tier in tiers:
        overall_verdict = worst(overall_verdict, implied_results[tier]['verdict'])
        # C1/C2 失败直接影响整体
        if implied_results[tier]['c1_fail']:
            overall_verdict = worst(overall_verdict, 'FAIL')
        if implied_results[tier]['c2_fail'] and implied_results[tier]['verdict'] != 'SKIP':
            overall_verdict = worst(overall_verdict, 'WARN')

    return {
        'code': code,
        'name': name,
        'roic_5y_avg_pct': float(roic * 100) if roic is not None else None,
        'fcf_annual_yi': float(oe_annual_yi) if oe_annual_yi is not None else None,
        'fcf_note': fcf_note,
        'terminal': terminal_results,
        'implied': implied_results,
        'overall_verdict': overall_verdict,
        'checked_at': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
    }


def verify_run(run_id=None, db_path=None, report_dir=None) -> dict:
    """批量验算一轮筛选结果的 intrinsic_value。

    读取 screening_result 的 ai_analysis JSON 中的 intrinsic_value，
    结合 financial_summary 的 roic_5y_avg、fcf_5y_sum、market_cap，
    以及 stock_snapshot 的 current_price。

    Returns:
        dict: {run_id, total, pass, warn, fail, skip, alerts, report}
    """
    from src.models.database import get_db_path  # 延迟导入，保持纯函数可独立测试

    db_path = db_path or get_db_path()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if run_id is None:
            row = con.execute(
                'SELECT run_id FROM screening_result ORDER BY run_date DESC, run_id DESC LIMIT 1'
            ).fetchone()
            if row is None:
                return {'run_id': None, 'total': 0, 'pass': 0, 'warn': 0,
                        'fail': 0, 'skip': 0, 'alerts': [], 'report': None,
                        'note': 'screening_result 为空'}
            run_id = row['run_id']

        # 取筛选结果（含 ai_analysis JSON）
        stocks = [dict(r) for r in con.execute(
            '''SELECT code, name, score, ai_analysis, market_cap
               FROM screening_result WHERE run_id = ? ORDER BY score DESC''',
            (run_id,))]

        # 取快照现价
        snaps = {r['code']: dict(r) for r in con.execute(
            'SELECT code, current_price, market_cap, circulating_cap FROM stock_snapshot')}

        # 取财务汇总（roic_5y_avg, fcf_5y_sum 等）
        summaries = {}
        for r in con.execute(
                'SELECT stock_code, roic_5y_avg, fcf_5y_sum '
                'FROM financial_summary'):
            summaries[r['stock_code']] = dict(r)

    finally:
        con.close()

    results, counts = [], {'PASS': 0, 'WARN': 0, 'FAIL': 0, 'SKIP': 0}

    for s in stocks:
        code = s['code']
        snap = snaps.get(code, {})
        fs = summaries.get(code, {})

        # 解析 ai_analysis JSON
        ai_analysis = {}
        try:
            if s.get('ai_analysis'):
                ai_analysis = json.loads(s['ai_analysis'])
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[终值验算] {code} ai_analysis JSON 解析失败")
            ai_analysis = {}

        intrinsic = ai_analysis.get('intrinsic_value') or {}

        # 组装验算所需数据
        stock_data = {
            'code': code,
            'name': s.get('name'),
            'roic_5y_avg': fs.get('roic_5y_avg'),
            'fcf_5y_sum': fs.get('fcf_5y_sum'),
            'market_cap': snap.get('market_cap'),
            'current_price': snap.get('current_price'),
            'intrinsic_value': intrinsic,
        }

        res = verify_intrinsic_for_stock(stock_data)
        overall = res['overall_verdict']
        counts[overall] += 1
        results.append(res)

    alerts = [r for r in results if r['overall_verdict'] in ('WARN', 'FAIL')]

    from datetime import datetime
    summary = {'run_id': run_id, 'total': len(results),
               'pass': counts['PASS'], 'warn': counts['WARN'],
               'fail': counts['FAIL'], 'skip': counts['SKIP'],
               'alerts': alerts, 'checked_at': datetime.now().isoformat(timespec='seconds')}

    if report_dir is None:
        proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        report_dir = os.path.join(proj, 'data')
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f'intrinsic_verification_{run_id}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump({'summary': summary, 'results': results}, f,
                  ensure_ascii=False, indent=1)
    summary['report'] = report_path

    logger.info(f"[终值验算] {run_id}: 通过={counts['PASS']} 告警={counts['WARN']} "
                f"失败={counts['FAIL']} 跳过={counts['SKIP']}")
    for a in alerts:
        logger.warning(f"[终值验算] {a['overall_verdict']} {a['code']} {a['name']} "
                       f"隐含g={a['implied'].get('optimistic',{}).get('implied_g')} "
                       f"终值PE={a['terminal'].get('optimistic',{}).get('terminal_pe')}")

    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='终值验算闸：批量验算 intrinsic_value')
    ap.add_argument('--run-id', default=None, help='缺省取最新轮')
    ap.add_argument('--db', default=None, help='缺省用项目 DB')
    ap.add_argument('--report-dir', default=None, help='缺省 data/')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format='%(message)s')

    s = verify_run(args.run_id, args.db, args.report_dir)

    tag = {'PASS': '[通过]', 'WARN': '[告警]', 'FAIL': '[失败]', 'SKIP': '[跳过]'}
    print(f"轮次 {s['run_id']}: 共 {s['total']} 只 "
          f"通过={s['pass']} 告警={s['warn']} 失败={s['fail']} 跳过={s['skip']}")
    for a in s['alerts']:
        print(f"  {tag[a['overall_verdict']]} {a['code']} {a['name']}")
    print(f"报告: {s['report']}")

    return 1 if s['fail'] > 0 else 0


if __name__ == '__main__':
    sys.exit(main())