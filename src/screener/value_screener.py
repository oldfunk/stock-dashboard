"""
价值投资量化筛选引擎
基于格雷厄姆/巴菲特风格的价值投资过滤条件
"""

import logging
from datetime import datetime
from typing import Optional

from src.models.database import (
    StockSnapshotDAO, ScreeningResultDAO, RunLogDAO,
)

logger = logging.getLogger(__name__)


class ValueScreener:
    """价值投资筛选器"""

    def __init__(self, config: dict):
        """
        config: {
            'max_pe': 20,
            'min_pe': 3,
            'max_pb': 3.5,
            'min_roe': 12,
            'min_revenue_growth': 5,
            'min_profit_growth': 5,
            'max_debt_ratio': 65,
            'min_market_cap': 50,
            'max_market_cap': 10000,
            'exclude_st': True,
            'max_candidates': 20,
        }
        """
        self.cfg = config
        self.snapshot_dao = StockSnapshotDAO()
        self.result_dao = ScreeningResultDAO()
        self.run_log_dao = RunLogDAO()

    def run(self) -> list[dict]:
        """
        执行筛选流程：
        1. 从 stock_snapshot 读取最新快照
        2. 应用价值投资过滤条件
        3. 综合评分排序
        4. 保存结果到 screening_result
        5. 返回 Top N 候选
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_date = datetime.now().strftime("%Y-%m-%d")
        self.run_log_dao.start_run(run_id)

        try:
            # 1. 读取最新快照
            snapshot_date = self.snapshot_dao.get_latest_snapshot_date()
            if not snapshot_date:
                logger.warning("[筛选] 无可用快照数据，请先运行数据采集")
                self.run_log_dao.complete_run(run_id, 0, 0, 0, "无可用快照数据")
                return []

            # 2. 从数据库读取所有股票（通过 DAO 获取）
            # 这里需要用临时手段读取全表
            import sqlite3
            from src.models.database import get_connection

            conn = get_connection()
            rows = conn.execute("""
                SELECT * FROM stock_snapshot WHERE snapshot_date = ?
            """, (snapshot_date,)).fetchall()
            conn.close()

            total_stocks = len(rows)
            logger.info(f"[筛选] 开始筛选 {total_stocks} 只股票...")

            # 3. 应用过滤条件
            candidates = []
            for row in rows:
                r = dict(row)
                reasons = self._check_value_criteria(r)
                if reasons:
                    score = self._calculate_score(r, reasons)
                    candidates.append({
                        'run_id': run_id,
                        'run_date': run_date,
                        'code': r['code'],
                        'name': r['name'],
                        'score': round(score, 1),
                        'pe': r.get('pe'),
                        'pb': r.get('pb'),
                        'roe': r.get('roe'),
                        'revenue_growth': r.get('revenue_growth'),
                        'profit_growth': r.get('profit_growth'),
                        'debt_ratio': r.get('debt_ratio'),
                        'market_cap': r.get('market_cap'),
                        'reason': '; '.join(reasons),
                    })

            # 4. 排序——综合评分从高到低
            candidates.sort(key=lambda x: x['score'], reverse=True)
            top_n = candidates[:self.cfg.get('max_candidates', 20)]

            # 5. 保存到数据库
            self.result_dao.save_batch(top_n)

            # 6. 更新运行日志
            self.run_log_dao.complete_run(
                run_id, total_stocks, len(top_n), 0
            )

            logger.info(
                f"[筛选] 完成: 全市场 {total_stocks} 只 → "
                f"候选 {len(candidates)} 只 → Top {len(top_n)} 只"
            )
            return top_n

        except Exception as e:
            logger.error(f"[筛选] 失败: {e}", exc_info=True)
            self.run_log_dao.complete_run(run_id, 0, 0, 0, str(e))
            return []

    def run_from_candidates(self, candidates: list[dict]) -> list[dict]:
        """
        对已采集+初筛的候选列表执行完整价值投资筛选 + 评分

        1. 调用 _check_value_criteria 做完整过滤（含 ROE/负债/增长等）
        2. 综合评分排序
        3. 保存结果到 screening_result
        4. 返回 Top N
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_date = datetime.now().strftime("%Y-%m-%d")
        self.run_log_dao.start_run(run_id)

        try:
            total = len(candidates)
            passed = []
            for s in candidates:
                reasons = self._check_value_criteria(s)
                if reasons:
                    score = self._calculate_score(s, reasons)
                    passed.append({
                        'run_id': run_id,
                        'run_date': run_date,
                        'code': s['code'],
                        'name': s['name'],
                        'score': round(score, 1),
                        'pe': s.get('pe'),
                        'pb': s.get('pb'),
                        'roe': s.get('roe'),
                        'revenue_growth': s.get('revenue_growth'),
                        'profit_growth': s.get('profit_growth'),
                        'debt_ratio': s.get('debt_ratio'),
                        'market_cap': s.get('market_cap'),
                        'reason': '; '.join(reasons),
                    })

            passed.sort(key=lambda x: x['score'], reverse=True)
            top_n = passed[:self.cfg.get('max_candidates', 20)]

            self.result_dao.save_batch(top_n)
            self.run_log_dao.complete_run(run_id, total, len(top_n), 0)

            logger.info(
                f"[筛选] 候选 {total} 只 → "
                f"通过 {len(passed)} 只 → Top {len(top_n)} 只"
            )
            return top_n

        except Exception as e:
            logger.error(f"[筛选] 失败: {e}", exc_info=True)
            self.run_log_dao.complete_run(run_id, 0, 0, 0, str(e))
            return []

    def _check_value_criteria(self, stock: dict) -> list[str]:
        """
        检查是否符合价值投资标准
        返回符合的理由列表（空=不符合）
        """
        reasons = []
        cfg = self.cfg

        # 排除 ST
        if cfg.get('exclude_st', True) and stock.get('is_st'):
            return []

        pe = stock.get('pe')
        pb = stock.get('pb')
        roe = stock.get('roe')
        revenue_growth = stock.get('revenue_growth')
        profit_growth = stock.get('profit_growth')
        debt_ratio = stock.get('debt_ratio')
        market_cap = stock.get('market_cap')

        # 检查各项指标
        if pe is None:
            return []

        # PE 范围
        max_pe = cfg.get('max_pe', 20)
        min_pe = cfg.get('min_pe', 3)
        if pe < min_pe or pe > max_pe:
            return []
        reasons.append(f"PE={pe}（{min_pe}~{max_pe}）")

        # PB
        max_pb = cfg.get('max_pb', 3.5)
        if pb is not None and pb > max_pb:
            return []
        if pb is not None and pb > 0:
            reasons.append(f"PB={pb}（<{max_pb}）")

        # ROE
        min_roe = cfg.get('min_roe', 12)
        if roe is not None and roe < min_roe:
            return []
        if roe is not None and roe > 0:
            reasons.append(f"ROE={roe}%（≥{min_roe}%）")

        # 营收增长
        min_rev_growth = cfg.get('min_revenue_growth', 5)
        if revenue_growth is not None and revenue_growth < min_rev_growth:
            return []
        if revenue_growth is not None and revenue_growth > 0:
            reasons.append(f"营收增长={revenue_growth}%")

        # 利润增长
        min_prof_growth = cfg.get('min_profit_growth', 5)
        if profit_growth is not None and profit_growth < min_prof_growth:
            return []
        if profit_growth is not None and profit_growth > 0:
            reasons.append(f"净利增长={profit_growth}%")

        # 负债率
        max_debt = cfg.get('max_debt_ratio', 65)
        if debt_ratio is not None and debt_ratio > max_debt:
            return []
        if debt_ratio is not None and debt_ratio >= 0:
            reasons.append(f"负债率={debt_ratio}%（<{max_debt}%）")

        # 市值
        min_mc = cfg.get('min_market_cap', 50)
        max_mc = cfg.get('max_market_cap', 10000)
        if market_cap is not None:
            # 市值单位是亿
            cap = market_cap
            if cap < min_mc or cap > max_mc:
                return []
            reasons.append(f"市值={cap}亿")

        return reasons

    def _calculate_score(self, stock: dict, reasons: list[str]) -> float:
        """
        计算综合评分 (0-100)
        加权打分：
        - ROE 权重最高（巴菲特最看重）
        - PE 估值折扣
        - 增长率
        - 负债率（越低越好）
        """
        score = 0.0
        weights = {
            'roe': 0.30,
            'pe_discount': 0.20,
            'growth': 0.25,
            'debt': 0.15,
            'pb': 0.10,
        }

        # ROE 评分 (0-100)
        roe = stock.get('roe') or 0
        if roe >= 30:
            roe_score = 100
        elif roe >= 20:
            roe_score = 80
        elif roe >= 15:
            roe_score = 60
        elif roe >= 12:
            roe_score = 40
        else:
            roe_score = 0
        score += roe_score * weights['roe']

        # PE 折扣评分 (0-100)
        # PE 越低越有安全边际
        pe = stock.get('pe') or 20
        if pe <= 5:
            pe_score = 100
        elif pe <= 10:
            pe_score = 80
        elif pe <= 15:
            pe_score = 60
        elif pe <= 20:
            pe_score = 40
        else:
            pe_score = 0
        score += pe_score * weights['pe_discount']

        # 增长评分 (0-100)
        rev_growth = stock.get('revenue_growth') or 0
        prof_growth = stock.get('profit_growth') or 0
        avg_growth = (rev_growth + prof_growth) / 2
        if avg_growth >= 30:
            growth_score = 100
        elif avg_growth >= 20:
            growth_score = 80
        elif avg_growth >= 10:
            growth_score = 60
        elif avg_growth >= 5:
            growth_score = 40
        else:
            growth_score = 0
        score += growth_score * weights['growth']

        # 负债率评分 (0-100)
        debt = stock.get('debt_ratio') or 100
        if debt <= 20:
            debt_score = 100
        elif debt <= 35:
            debt_score = 80
        elif debt <= 50:
            debt_score = 60
        elif debt <= 65:
            debt_score = 40
        else:
            debt_score = 0
        score += debt_score * weights['debt']

        # PB 评分 (0-100)
        pb = stock.get('pb') or 10
        if pb <= 1:
            pb_score = 100
        elif pb <= 1.5:
            pb_score = 80
        elif pb <= 2:
            pb_score = 60
        elif pb <= 3.5:
            pb_score = 40
        else:
            pb_score = 0
        score += pb_score * weights['pb']

        return score


def run_screener(config: dict, candidates: list[dict] = None,
                 run_id: str = None, run_date: str = None) -> list[dict]:
    """
    对候选股票进行评分排序，保存 top N 到数据库。

    Args:
        config: 完整配置字典
        candidates: 预筛选的候选股列表（如果为 None，则从数据库读取最新快照）
        run_id: 运行批次 ID
        run_date: 运行日期
    """
    conditions = config.get('screener', {}).get('conditions', {})
    screener = ValueScreener(conditions)

    if candidates is not None:
        # 使用已预筛选+补充财务数据的候选股
        from src.models.database import ScreeningResultDAO, RunLogDAO
        result_dao = ScreeningResultDAO()
        if not run_id:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not run_date:
            run_date = datetime.now().strftime("%Y-%m-%d")

        scored = []
        for c in candidates:
            reasons = screener._check_value_criteria(c)
            if reasons:
                score = screener._calculate_score(c)
                scored.append({
                    'run_id': run_id,
                    'run_date': run_date,
                    'code': c['code'],
                    'name': c['name'],
                    'score': round(score, 1),
                    'pe': c.get('pe'),
                    'pb': c.get('pb'),
                    'roe': c.get('roe'),
                    'revenue_growth': c.get('revenue_growth'),
                    'profit_growth': c.get('profit_growth'),
                    'debt_ratio': c.get('debt_ratio'),
                    'market_cap': c.get('market_cap'),
                    'reason': '; '.join(reasons),
                })

        scored.sort(key=lambda x: x['score'], reverse=True)
        top_n = scored[:conditions.get('max_candidates', 20)]

        if top_n:
            result_dao.save_batch(top_n)
            # 更新 run_log 中的筛选数量
            from src.models.database import get_connection
            conn = get_connection()
            conn.execute(
                "UPDATE run_log SET screened_count = ? WHERE run_id = ?",
                (len(top_n), run_id)
            )
            conn.commit()
            conn.close()

        return top_n
    else:
        # 原始行为：从数据库读取快照
        return screener.run()
