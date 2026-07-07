"""价值投资量化筛选引擎 — AI Berkshire 完整版

基于 AI Berkshire 的 7条硬性门规 + 3条豁免规则 + 镜子测试预备。

对外暴露：
- ValueScreener 类：核心过滤 + 评分逻辑（无副作用，不写库）
- run_screener() 函数：编排「加载历史→过滤→评分→排序→持久化」
"""

import logging
from src.models.database import ScreeningResultDAO, RunLogDAO, FinancialSummaryDAO
from src.utils import now_cn

logger = logging.getLogger(__name__)


# ── AI Berkshire 规则定义 ──

def _check_7_gates(stock: dict, cfg: dict) -> list[str]:
    """AI Berkshire 7条门规 + 3条豁免 + 季节性OCF容差。

    返回符合的理由列表（空列表 = 不符合，一票否决）。
    """
    reasons: list[str] = []
    screen = cfg.get('screener', {}).get('conditions', {})

    # ── 排除 ST ──
    if screen.get('exclude_st', True) and stock.get('is_st'):
        return []

    # ── 规则 0：PE（基础估值过滤）──
    pe = stock.get('pe')
    if pe is None:
        return []
    max_pe = screen.get('max_pe', 20)
    min_pe = screen.get('min_pe', 3)
    if pe < min_pe or pe > max_pe:
        return []
    reasons.append(f"PE={pe}（{min_pe}~{max_pe}）")

    # ── 规则 1：ROE ≥ 5%（当前）, 历史均值参考 ──
    min_roe = screen.get('min_roe', 5)
    roe = stock.get('roe')
    if roe is not None and roe < min_roe:
        return []
    if roe is not None and roe > 0:
        reasons.append(f"ROE={roe}%（≥{min_roe}%）")

    # 5年平均ROE（AI Berkshire: 10年平均<8%排除，我们取5年）
    roe_5y = stock.get('roe_5y_avg')
    if roe_5y is not None and roe_5y < 8:
        # 豁免A：上市不足8年且处于高增长期（增长>20%）
        rev_g = stock.get('revenue_growth') or 0
        if rev_g < 20:
            return []
        reasons.append(f"5年均ROE={roe_5y}%（<8%，豁免：高增长{rev_g}%新期公司）")
    elif roe_5y is not None:
        reasons.append(f"5年均ROE={roe_5y}%")

    # ── 规则 2：OCF/NI ≥ 0.7 代理（用OCF/股正数 + 历史OCF为正的年数）──
    # AI Berkshire: 5年累计FCF为负排除 + OCF/NI < 0.7排除
    # 代理：OCF/股>0 + 多数年份OCF为正
    ocf = stock.get('ocf_per_share')
    ocf_pos_years = stock.get('ocf_positive_years')
    if ocf is not None:
        if ocf <= 0:
            # 豁免B：战略投入期（高毛利率+高营收增长可豁免）
            gm = stock.get('gross_margin') or 0
            rev_g = stock.get('revenue_growth') or 0
            if gm >= 30 and rev_g >= 20:
                reasons.append(
                    f"OCF/股={ocf}（≤0，豁免：高毛利率{gm}%+高增长{rev_g}%投入期）")
            else:
                return []
        else:
            # OCF为正，再看历史多数年份是否也为正
            if ocf_pos_years is not None and ocf_pos_years < 3 and stock.get('roe_5y_count', 0) >= 4:
                return []  # 多数年份OCF为负 → 排除
            reasons.append(f"OCF/股={ocf}（正数）")

    # ── 规则 3：净利率 ≥ 5%（当前 + 5年均值）──
    min_nm = screen.get('min_net_margin', 5)
    nm_5y = stock.get('net_margin_5y_avg')
    # 用当前毛利率近似判断（无当前净利率字段，但有毛利率和5年均净利率）
    gm_current = stock.get('gross_margin')
    if nm_5y is not None and nm_5y < min_nm:
        # 豁免C：主动低利润率模式（毛利率>30%说明产品有差异化，故意压低净利扩张）
        if gm_current and gm_current >= 30:
            reasons.append(f"5年均净利率={nm_5y}%（<{min_nm}%，豁免：高毛利率{gm_current}%主动压利）")
        else:
            return []
    elif nm_5y is not None:
        reasons.append(f"5年均净利率={nm_5y}%")

    # ── 规则 4：毛利率 ≥ 15%（当前）──
    min_gm = screen.get('min_gross_margin', 15)
    gross_margin = stock.get('gross_margin')
    if gross_margin is not None:
        if gross_margin < min_gm:
            # 豁免D：高周转薄利模式（ROE>20% 说明资本回报率高，可容忍毛利率低）
            roe = stock.get('roe') or 0
            if roe >= 20:
                reasons.append(
                    f"毛利率={gross_margin}%（<{min_gm}%，豁免：高ROE={roe}%薄利模式）")
            else:
                return []
        else:
            reasons.append(f"毛利率={gross_margin}%（≥{min_gm}%）")

    # ── 规则 5：营收增长 ≥ 0 + 净利增长 ≥ 0 ──
    min_rev = screen.get('min_revenue_growth', 0)
    rev_growth = stock.get('revenue_growth')
    if rev_growth is not None and rev_growth < min_rev:
        return []
    if rev_growth is not None and rev_growth > 0:
        reasons.append(f"营收增长={rev_growth}%")

    min_prof = screen.get('min_profit_growth', 0)
    prof_growth = stock.get('profit_growth')
    if prof_growth is not None and prof_growth < min_prof:
        return []
    if prof_growth is not None and prof_growth > 0:
        reasons.append(f"净利增长={prof_growth}%")

    # ── 规则 6：负债率 < 65% ──
    max_debt = screen.get('max_debt_ratio', 65)
    debt = stock.get('debt_ratio')
    if debt is not None and debt > max_debt:
        return []
    if debt is not None and debt >= 0:
        reasons.append(f"负债率={debt}%（<{max_debt}%）")

    # ── 规则 7：利息覆盖 ≥ 2x ──
    intcov = stock.get('intcov_5y_avg')
    if intcov is not None and intcov < 2:
        return []
    elif intcov is not None and intcov >= 2:
        reasons.append(f"5年均利息覆盖={intcov}x")

    # ── 规则 8：5年累计FCF为正 ──
    fcf_5y = stock.get('fcf_5y_sum')
    if fcf_5y is not None and fcf_5y <= 0:
        # 小额负FCF（<1亿）可以豁免（可能是资本开支高峰年）
        if abs(fcf_5y) > 100_000_000:
            return []
        reasons.append(f"5年FCF累计={fcf_5y:.0f}（小额负值豁免）")
    elif fcf_5y is not None and fcf_5y > 0:
        fcf_yi = fcf_5y / 1e8
        reasons.append(f"5年FCF累计={fcf_yi:.1f}亿")

    # ── 规则 9：5年股本稀释 ≤ 20% ──
    dilution = stock.get('share_dilution_5y')
    if dilution is not None and dilution > 20:
        return []
    elif dilution is not None:
        reasons.append(f"5年稀释={dilution}%")

    # ── 规则 7：市值 ──
    min_mc = screen.get('min_market_cap', 30)
    max_mc = screen.get('max_market_cap', 50000)
    mc = stock.get('market_cap')
    if mc is not None:
        if mc < min_mc or mc > max_mc:
            return []
        reasons.append(f"市值={mc}亿")

    # PB 辅助参考
    max_pb = screen.get('max_pb', 3.5)
    pb = stock.get('pb')
    if pb is not None and pb > max_pb:
        return []
    if pb is not None and pb > 0:
        reasons.append(f"PB={pb}（<{max_pb}）")

    return reasons


def _calculate_moat_score(stock: dict) -> float:
    """AI Berkshire 五维评分 (0-100)。

    权重反映：护城河 > 估值 > 增长 > 财务健康 > 市场验证
    """
    weights = {'roe': 0.30, 'pe': 0.20, 'growth': 0.20,
               'debt': 0.15, 'gm': 0.15}
    score = 0.0

    # 1) ROE（资本回报率 — 核心护城河指标）
    roe = stock.get('roe') or 0
    roe_5y = stock.get('roe_5y_avg') or 0
    roe_combined = max(roe, roe_5y)  # 取当前和5年均值中较好的
    if roe_combined >= 30:
        roe_s = 100
    elif roe_combined >= 20:
        roe_s = 85
    elif roe_combined >= 15:
        roe_s = 65
    elif roe_combined >= 12:
        roe_s = 45
    elif roe_combined >= 8:
        roe_s = 25
    else:
        roe_s = 0
    score += roe_s * weights['roe']

    # 2) PE（安全边际 — 越低越高分）
    pe = stock.get('pe') or 20
    if pe <= 5:
        pe_s = 100
    elif pe <= 8:
        pe_s = 90
    elif pe <= 10:
        pe_s = 76
    elif pe <= 15:
        pe_s = 55
    elif pe <= 20:
        pe_s = 35
    else:
        pe_s = 0
    score += pe_s * weights['pe']

    # 3) 增长（营收+净利均衡增长）
    rev_g = stock.get('revenue_growth') or 0
    prof_g = stock.get('profit_growth') or 0
    avg_g = (rev_g + prof_g) / 2
    if avg_g >= 30:
        g_s = 100
    elif avg_g >= 20:
        g_s = 80
    elif avg_g >= 10:
        g_s = 60
    elif avg_g >= 5:
        g_s = 40
    else:
        g_s = 20  # 零增长不扣光（价值股特征）
    score += g_s * weights['growth']

    # 4) 负债率（财务稳健）
    debt = stock.get('debt_ratio') or 100
    if debt <= 20:
        d_s = 100
    elif debt <= 35:
        d_s = 80
    elif debt <= 50:
        d_s = 60
    elif debt <= 65:
        d_s = 40
    else:
        d_s = 0
    score += d_s * weights['debt']

    # 5) 毛利率（护城河补充 — 定价权）
    gm = stock.get('gross_margin') or 0
    gm_5y = stock.get('gross_margin_5y_avg') or 0
    gm_combined = max(gm, gm_5y)
    if gm_combined >= 60:
        gm_s = 100
    elif gm_combined >= 40:
        gm_s = 80
    elif gm_combined >= 30:
        gm_s = 60
    elif gm_combined >= 15:
        gm_s = 40
    elif gm_combined >= 10:
        gm_s = 20  # 低毛利但有ROE豁免
    else:
        gm_s = 0
    score += gm_s * weights['gm']

    return round(score, 1)


# ── 主类 ──

class ValueScreener:
    """AI Berkshire 价值投资筛选器（纯逻辑，无 IO 副作用）。

    check_criteria 使用 stock 内嵌入的 5年历史字段（由 score_candidates 预加载）。
    """

    def __init__(self, config: dict):
        self.cfg = config

    def check_criteria(self, stock: dict) -> list[str]:
        return _check_7_gates(stock, self.cfg)

    def calculate_score(self, stock: dict) -> float:
        return _calculate_moat_score(stock)

    def score_candidates(self, candidates: list[dict],
                         run_id: str, run_date: str) -> list[dict]:
        """过滤+评分+排序，返回 Top N（不写库）。

        自动从 financial_summary 加载 5年历史均值嵌入每个 stock。
        """
        # 批量加载历史汇总（如果数据库中有的话）
        codes = [c['code'] for c in candidates]
        try:
            summaries = FinancialSummaryDAO().get_batch(codes)
        except Exception:
            summaries = {}

        scored = []
        for c in candidates:
            # 嵌入历史数据到 stock dict（无冲突字段）
            summary = summaries.get(c['code'], {})
            for k in ('roe_5y_avg', 'roe_5y_count', 'gross_margin_5y_avg',
                      'net_margin_5y_avg', 'ocf_5y_trend', 'ocf_latest',
                      'ocf_positive_years', 'debt_ratio_latest', 'data_years'):
                if summary.get(k) is not None:
                    c[k] = summary.get(k)

            reasons = self.check_criteria(c)
            if not reasons:
                continue

            score = self.calculate_score(c)

            # 数据质量标记
            data_years = c.get('data_years', '')
            has_history = bool(data_years)
            if has_history:
                reasons.append(f"财务数据覆盖={data_years}")

            scored.append({
                'run_id': run_id,
                'run_date': run_date,
                'code': c['code'],
                'name': c['name'],
                'score': score,
                'pe': c.get('pe'),
                'pb': c.get('pb'),
                'roe': c.get('roe'),
                'gross_margin': c.get('gross_margin'),
                'net_margin': c.get('net_margin'),
                'ocf_per_share': c.get('ocf_per_share'),
                'revenue_growth': c.get('revenue_growth'),
                'profit_growth': c.get('profit_growth'),
                'debt_ratio': c.get('debt_ratio'),
                'market_cap': c.get('market_cap'),
                'reason': '; '.join(reasons),
            })

        scored.sort(key=lambda x: x['score'], reverse=True)
        max_n = self.cfg.get('max_candidates', 20) if isinstance(self.cfg, dict) else 20
        return scored[:max_n]


def run_screener(config: dict, candidates: list[dict],
                 run_id: str = None, run_date: str = None) -> list[dict]:
    """候选股评分排序 + 持久化到筛选结果表。"""
    screen_cfg = config.get('screener', {}).get('conditions', {})
    screener = ValueScreener(screen_cfg)

    if run_id is None:
        run_id = now_cn().strftime("%Y%m%d_%H%M%S")
    if run_date is None:
        run_date = now_cn().strftime("%Y-%m-%d")

    top_n = screener.score_candidates(candidates, run_id, run_date)

    result_dao = ScreeningResultDAO()
    if top_n:
        result_dao.save_batch(top_n)
        RunLogDAO().update_screened_count(run_id, len(top_n))

    logger.info(f"[筛选] 候选 {len(candidates)} 只 → 通过评分 {len(top_n)} 只")
    return top_n
