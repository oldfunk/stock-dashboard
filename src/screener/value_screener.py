"""
价值投资量化筛选引擎
基于格雷厄姆/巴菲特风格的价值投资过滤条件

对外只暴露：
- ValueScreener 类：核心的过滤 + 评分逻辑（无副作用，不写库）
- run_screener() 函数：编排「评分 → 排序 → 持久化」的入口
"""

import logging

from src.models.database import ScreeningResultDAO, RunLogDAO
from src.utils import now_cn

logger = logging.getLogger(__name__)


class ValueScreener:
    """价值投资筛选器（纯逻辑，无 IO 副作用）。

    config 形如::

        {
            'max_pe': 20, 'min_pe': 3, 'max_pb': 3.5,
            'min_roe': 5, 'min_revenue_growth': 0, 'min_profit_growth': 0,
            'max_debt_ratio': 65, 'min_market_cap': 30, 'max_market_cap': 50000,
            'exclude_st': True, 'max_candidates': 20,
        }

    默认值与 config/config.yaml 保持一致，避免配置缺失时行为偏离。
    """

    def __init__(self, config: dict):
        self.cfg = config or {}

    def check_criteria(self, stock: dict) -> list[str]:
        """
        检查是否符合价值投资标准。
        返回符合的理由列表（空列表 = 不符合）。
        """
        reasons: list[str] = []
        cfg = self.cfg

        # 排除 ST
        if cfg.get('exclude_st', True) and stock.get('is_st'):
            return []

        pe = stock.get('pe')
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
        pb = stock.get('pb')
        if pb is not None and pb > max_pb:
            return []
        if pb is not None and pb > 0:
            reasons.append(f"PB={pb}（<{max_pb}）")

        # ROE（默认 5，与 config.yaml 一致）
        min_roe = cfg.get('min_roe', 5)
        roe = stock.get('roe')
        if roe is not None and roe < min_roe:
            return []
        if roe is not None and roe > 0:
            reasons.append(f"ROE={roe}%（≥{min_roe}%）")

        # 营收增长（默认 0，允许稳定型价值股）
        min_rev_growth = cfg.get('min_revenue_growth', 0)
        revenue_growth = stock.get('revenue_growth')
        if revenue_growth is not None and revenue_growth < min_rev_growth:
            return []
        if revenue_growth is not None and revenue_growth > 0:
            reasons.append(f"营收增长={revenue_growth}%")

        # 利润增长（默认 0）
        min_prof_growth = cfg.get('min_profit_growth', 0)
        profit_growth = stock.get('profit_growth')
        if profit_growth is not None and profit_growth < min_prof_growth:
            return []
        if profit_growth is not None and profit_growth > 0:
            reasons.append(f"净利增长={profit_growth}%")

        # 负债率
        max_debt = cfg.get('max_debt_ratio', 65)
        debt_ratio = stock.get('debt_ratio')
        if debt_ratio is not None and debt_ratio > max_debt:
            return []
        if debt_ratio is not None and debt_ratio >= 0:
            reasons.append(f"负债率={debt_ratio}%（<{max_debt}%）")

        # 市值
        min_mc = cfg.get('min_market_cap', 30)
        max_mc = cfg.get('max_market_cap', 50000)
        market_cap = stock.get('market_cap')
        if market_cap is not None:
            if market_cap < min_mc or market_cap > max_mc:
                return []
            reasons.append(f"市值={market_cap}亿")

        return reasons

    def calculate_score(self, stock: dict) -> float:
        """
        综合评分 (0-100)，加权：
        - ROE 权重最高（巴菲特最看重）
        - PE 估值折扣
        - 增长率
        - 负债率（越低越好）
        - PB
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

        # PE 折扣评分 (0-100) —— PE 越低越有安全边际
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

    def score_candidates(self, candidates: list[dict],
                         run_id: str, run_date: str) -> list[dict]:
        """对候选股执行过滤+评分+排序，返回 Top N 结果字典列表（不写库）。

        返回的字典字段与 ScreeningResultDAO.save_batch 兼容。
        """
        scored = []
        for c in candidates:
            reasons = self.check_criteria(c)
            if not reasons:
                continue
            score = self.calculate_score(c)
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
        max_candidates = self.cfg.get('max_candidates', 20)
        return scored[:max_candidates]


def run_screener(config: dict, candidates: list[dict],
                 run_id: str = None, run_date: str = None) -> list[dict]:
    """
    对候选股评分排序 + 持久化 Top N 到 screening_result。

    Args:
        config: 完整配置字典（取 config['screener']['conditions']）
        candidates: 已采集+初筛+财务补充的候选股列表
        run_id: 运行批次 ID；为 None 时按当前时间生成
        run_date: 运行日期；为 None 时取今天
    """
    conditions = config.get('screener', {}).get('conditions', {})
    screener = ValueScreener(conditions)

    if run_id is None:
        run_id = now_cn().strftime("%Y%m%d_%H%M%S")
    if run_date is None:
        run_date = now_cn().strftime("%Y-%m-%d")

    top_n = screener.score_candidates(candidates, run_id, run_date)

    result_dao = ScreeningResultDAO()
    if top_n:
        result_dao.save_batch(top_n)
        # 同步 run_log 的筛选数量
        RunLogDAO().update_screened_count(run_id, len(top_n))

    logger.info(
        f"[筛选] 候选 {len(candidates)} 只 → 通过评分 {len(top_n)} 只"
    )
    return top_n
