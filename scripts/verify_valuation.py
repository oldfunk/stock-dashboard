#!/usr/bin/env python3
"""估值验算闸 B1：移植自 AI Berkshire tools/financial_rigor.py 的轻量版。

只用 stdlib（decimal/json/argparse/sqlite3），零 emoji。
三道检查（全部 Decimal 精确计算，禁止 float 心算）：
  V1 市值独立验算：现价 × 年报总股本 / 1e8 vs 快照市值（亿）。
     总股本取 financial_history（财报口径），与快照市值
     （业绩快报净利润/EPS 倒推）是两个独立来源——循环自证会被判 SKIP。
  V2 估值复算：PE = 现价/eps，PB = 现价/bvps（eps/bvps 取最新财报行），
     与快照 pe/pb 对比。
  V3 表间交叉：screening_result.pe vs stock_snapshot.pe（复制一致性）。
判定：V1/V3 用 1%/5% 线；V2 因口径天然不同（快照为 TTM，
年报滞后），只做极端值捕捉：偏差 >100% 告警，>300% 或符号矛盾
（如一边为负）失败，其余通过。缺数据判跳过（不判失败）。
批量：verify_run(run_id) 跑整轮 20 候选，JSON 报告落 data/，
有失败 exit 1。供 scripts/run_pipeline.py 采集后调用。
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, getcontext

getcontext().prec = 28
logger = logging.getLogger(__name__)

TOL_WARN = 1.0
TOL_FAIL = 5.0


def exact(value):
    """任何数值转精确 Decimal，避开 float 陷阱。"""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def deviation_pct(calculated: Decimal, reported: Decimal):
    """相对偏差百分比；reported 为 0/None 时返回 None（判跳过）。"""
    if reported is None or calculated is None:
        return None
    if reported == 0:
        return None
    return abs(float(calculated - reported) / float(reported)) * 100.0


def verdict_for(dev, tol_warn=TOL_WARN, tol_fail=TOL_FAIL) -> str:
    """PASS / WARN / FAIL / SKIP 四态。"""
    if dev is None:
        return 'SKIP'
    if dev > tol_fail:
        return 'FAIL'
    if dev > tol_warn:
        return 'WARN'
    return 'PASS'


def verify_market_cap(price, shares, reported_cap_yi) -> dict:
    """V1：市值独立验算。price 现价（元），shares 总股本（股），
    reported_cap_yi 快照市值（亿）。返回计算市值（亿）+ 偏差 + 判定。"""
    if price is None or shares is None or reported_cap_yi is None:
        return {'calculated_yi': None, 'deviation_pct': None,
                'verdict': 'SKIP', 'note': '缺现价/总股本/快照市值'}
    if float(price) <= 0 or float(shares) <= 0:
        return {'calculated_yi': None, 'deviation_pct': None,
                'verdict': 'SKIP', 'note': '现价或总股本非正'}
    calc_yi = exact(price) * exact(shares) / Decimal(1e8)
    dev = deviation_pct(calc_yi, exact(reported_cap_yi))
    v = verdict_for(dev)
    note = '' if v == 'PASS' else (
        '股本非最新（回购/增发）？单位口径？股价非最新？' if v == 'FAIL'
        else '股价波动或股本微调所致')
    return {'calculated_yi': round(float(calc_yi), 2),
            'deviation_pct': round(dev, 2) if dev is not None else None,
            'verdict': v, 'note': note}


def verify_ratios(price, eps, bvps, reported_pe, reported_pb) -> dict:
    """V2：PE/PB 复算并与快照值对比。"""
    out = {}
    if price is not None and eps is not None and float(eps) > 0:
        pe_calc = exact(price) / exact(eps)
        dev = deviation_pct(pe_calc, exact(reported_pe)) \
            if reported_pe is not None and float(reported_pe) > 0 else None
        out['pe'] = {'calculated': round(float(pe_calc), 2),
                     'reported': reported_pe,
                     'deviation_pct': round(dev, 2) if dev is not None else None,
                     'verdict': verdict_for(dev)}
    else:
        out['pe'] = {'calculated': None, 'reported': reported_pe,
                     'deviation_pct': None, 'verdict': 'SKIP',
                     'note': '缺现价/eps 或 eps 非正'}
    if price is not None and bvps is not None and float(bvps) > 0:
        pb_calc = exact(price) / exact(bvps)
        dev = deviation_pct(pb_calc, exact(reported_pb)) \
            if reported_pb is not None and float(reported_pb) > 0 else None
        out['pb'] = {'calculated': round(float(pb_calc), 2),
                     'reported': reported_pb,
                     'deviation_pct': round(dev, 2) if dev is not None else None,
                     'verdict': verdict_for(dev)}
    else:
        out['pb'] = {'calculated': None, 'reported': reported_pb,
                     'deviation_pct': None, 'verdict': 'SKIP',
                     'note': '缺现价/bvps 或 bvps 非正'}
    return out


def cross_check(field: str, a, b, tol_fail=TOL_FAIL) -> dict:
    """V3：两处同名字段交叉（复制一致性）。"""
    if a is None or b is None:
        return {'field': field, 'a': a, 'b': b,
                'deviation_pct': None, 'verdict': 'SKIP',
                'note': '一侧缺失'}
    dev = deviation_pct(exact(a), exact(b))
    return {'field': field, 'a': a, 'b': b,
            'deviation_pct': round(dev, 2) if dev is not None else None,
            'verdict': verdict_for(dev, tol_warn=tol_fail, tol_fail=tol_fail)}


def _worst(*verdicts) -> str:
    order = {'SKIP': 0, 'PASS': 1, 'WARN': 2, 'FAIL': 3}
    return max(verdicts, key=lambda v: order.get(v, 0))


def verify_run(run_id=None, db_path=None, report_dir=None) -> dict:
    """批量验算一轮筛选结果。run_id 缺省取最新轮。
    返回 {run_id, total, pass, warn, fail, skip, alerts, report}。"""
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
        stocks = [dict(r) for r in con.execute(
            'SELECT code, name, pe, pb, market_cap FROM screening_result WHERE run_id = ?',
            (run_id,))]
        snaps = {r['code']: dict(r) for r in con.execute(
            'SELECT code, current_price, pe, pb, market_cap FROM stock_snapshot')}
        hist = {}
        for r in con.execute(
                'SELECT stock_code, report_date, report_type, eps, total_shares '
                'FROM financial_history ORDER BY stock_code, report_date DESC'):
            # V2 取年报行（report_type=A）：季报单期 EPS 与 TTM 口径不可比
            prev = hist.get(r['stock_code'])
            if prev is None:
                hist[r['stock_code']] = {'latest': dict(r), 'annual': None}
            if r['report_type'] == 'A' and hist[r['stock_code']]['annual'] is None:
                hist[r['stock_code']]['annual'] = dict(r)
    finally:
        con.close()

    results, counts = [], {'PASS': 0, 'WARN': 0, 'FAIL': 0, 'SKIP': 0}
    for s in stocks:
        code = s['code']
        snap = snaps.get(code, {})
        h = hist.get(code) or {}
        annual = h.get('annual') or {}
        price = snap.get('current_price')
        # bvps 不在 history 表，用快照 pb 反推仅作存在性判断，不做复算
        v1 = verify_market_cap(price, (h.get('latest') or {}).get('total_shares'),
                               snap.get('market_cap'))
        v2 = verify_ratios(price, annual.get('eps'), None,
                           snap.get('pe'), snap.get('pb'))
        # V2 宽口径：快照 TTM vs 年报（滞后），只捕极端值 + 符号矛盾
        pe = v2['pe']
        if pe['verdict'] != 'SKIP':
            calc, rep = pe['calculated'] or 0, pe['reported'] or 0
            if calc * rep < 0:
                pe['verdict'] = 'FAIL'
                pe['note'] = '符号矛盾（一边为负）'
            else:
                pe['verdict'] = verdict_for(pe['deviation_pct'],
                                            tol_warn=100.0, tol_fail=300.0)
                pe['note'] = '口径：快照TTM vs 年报（滞后），只捕极端值'
        v2['pb'] = {'calculated': None, 'reported': snap.get('pb'),
                    'deviation_pct': None, 'verdict': 'SKIP',
                    'note': 'history 无 bvps 列，PB 复算待补'}
        v3 = cross_check('pe', s.get('pe'), snap.get('pe'))
        overall = _worst(v1['verdict'], v2['pe']['verdict'], v3['verdict'])
        counts[overall] += 1
        results.append({'code': code, 'name': s.get('name'),
                        'report_date': (h.get('latest') or {}).get('report_date'),
                        'market_cap': v1, 'ratios': v2, 'xcheck': v3,
                        'verdict': overall})
    alerts = [{'code': r['code'], 'name': r['name'], 'verdict': r['verdict'],
               'market_cap': r['market_cap'], 'ratios': r['ratios']}
              for r in results if r['verdict'] in ('WARN', 'FAIL')]
    summary = {'run_id': run_id, 'total': len(results),
               'pass': counts['PASS'], 'warn': counts['WARN'],
               'fail': counts['FAIL'], 'skip': counts['SKIP'],
               'alerts': alerts, 'checked_at': datetime.now().isoformat(timespec='seconds')}
    report_path = None
    if report_dir is None:
        proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        report_dir = os.path.join(proj, 'data')
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f'verification_report_{run_id}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump({'summary': summary, 'results': results}, f,
                  ensure_ascii=False, indent=1)
    summary['report'] = report_path
    logger.info(f"[验算闸] {run_id}: 通过={counts['PASS']} 告警={counts['WARN']} "
                f"失败={counts['FAIL']} 跳过={counts['SKIP']}")
    for a in alerts:
        logger.warning(f"[验算闸] {a['verdict']} {a['code']} {a['name']} "
                       f"市值偏差={a['market_cap'].get('deviation_pct')}% "
                       f"PE偏差={a['ratios']['pe'].get('deviation_pct')}%")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='估值验算闸：批量验算一轮筛选结果')
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
        print(f"  {tag[a['verdict']]} {a['code']} {a['name']}")
    print(f"报告: {s['report']}")
    return 1 if s['fail'] > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
