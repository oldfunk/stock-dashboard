"""价值投资量化筛选引擎 — AI Berkshire 完整版

基于 AI Berkshire 的 7条硬性门规 + 3条豁免规则 + 镜子测试预备。

对外暴露：
- ValueScreener 类：核心过滤 + 评分逻辑（无副作用，不写库）
- run_screener() 函数：编排「加载历史→过滤→评分→排序→持久化」
"""

import json
import logging
from pathlib import Path

import yaml

from src.models.database import ScreeningResultDAO, RunLogDAO, FinancialSummaryDAO
from src.utils import now_cn

logger = logging.getLogger(__name__)

STRATEGY_KEYS = ('growth', 'dividend', 'turnaround')

_DEFAULT_STRATEGIES_PATH = (
    Path(__file__).resolve().parent.parent.parent / 'config' / 'strategies.yaml'
)


def load_strategies(path: str | Path | None = None) -> dict:
    """读取 config/strategies.yaml，返回三策略阈值配置。

    缺文件/解析失败返回 {}（调用方退化为单策略行为），不抛异常。
    """
    p = Path(path) if path else _DEFAULT_STRATEGIES_PATH
    try:
        with open(p, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    except (OSError, ValueError, AttributeError):
        logger.warning(f'[策略] 策略配置文件读取失败: {p}')
        return {}
    return {k: v for k, v in data.items() if k in STRATEGY_KEYS and isinstance(v, dict)}


def _as_percent(value: float | None, threshold: float) -> float | None:
    """阈值归一化：strategies.yaml 用小数（如 0.30），行情字段用百分比如 40.0。

    阈值 <1 且字段值 >1 时视为小数口径，放大 100 倍对齐。
    """
    if value is None:
        return None
    if abs(threshold) < 1 < abs(value):
        return threshold * 100
    return threshold


def _check_strategy_thresholds(stock: dict, thresholds: dict) -> list[str]:
    """单策略阈值检查：返回命中理由，空列表 = 未命中该策略。

    缺失数据的阈值项直接跳过（不否决）：策略层叠加在 7 门基础质量之上，
    只对可验证项加严，不因上游字段缺失误杀。
    """
    reasons: list[str] = []
    t = thresholds or {}

    def _num(*keys):
        for k in keys:
            v = stock.get(k)
            if v is not None:
                return v
        return None

    checks = [
        ('roe_avg_5y_min', _num('roe_5y_avg', 'roe'), 'ge', 'ROE5年均'),
        ('pe_max', _num('pe'), 'le', 'PE'),
        ('pe_ttm_max', _num('pe'), 'le', 'PE_TTM'),
        ('gross_margin_min', _num('gross_margin_5y_avg', 'gross_margin'), 'ge', '毛利率'),
        ('net_margin_min', _num('net_margin_5y_avg', 'net_margin'), 'ge', '净利率'),
        ('debt_ratio_max', _num('debt_ratio'), 'le', '负债率'),
        ('dividend_yield_min', _num('dividend_yield'), 'ge', '股息率'),
        ('roe_volatility_max', _num('roe_volatility'), 'le', 'ROE波动'),
    ]
    for key, val, op, label in checks:
        if key not in t or val is None:
            continue
        bound = _as_percent(val, t[key])
        if op == 'ge' and val < bound:
            return []
        if op == 'le' and val > bound:
            return []
        reasons.append(f'{label}{val}(策略线{bound})')

    # 负债权益比：由负债率推导 D/E = debt/(100-debt)
    if 'debt_to_equity_max' in t:
        debt = _num('debt_ratio')
        if debt is not None and 0 < debt < 100:
            de = debt / (100 - debt)
            if de > t['debt_to_equity_max']:
                return []
            reasons.append(f'负债权益比{de:.2f}')

    # 分红率：无上游字段时跳过（只在可验证时加严）
    if 'payout_ratio_max' in t:
        payout = _num('payout_ratio')
        if payout is not None and payout > t['payout_ratio_max'] * (
                100 if abs(t['payout_ratio_max']) < 1 < abs(payout) else 1):
            return []

    # FCF 收益率：年均FCF/市值，缺数据跳过
    if 'fcf_yield_min' in t:
        fcf_sum = _num('fcf_5y_sum')
        mc = _num('market_cap')
        if fcf_sum is not None and mc:
            wear = fcf_sum / 5 / (mc * 1e8)
            if wear < t['fcf_yield_min']:
                return []
            reasons.append(f'FCF收益率{wear:.3f}')

    return reasons


# ── AI Berkshire 规则定义 ──

def _data_years_span(data_years) -> int | None:
    """'2020-2026' → 6；解析失败返回 None（调用方判不豁免）。"""
    try:
        a, b = str(data_years).split('-')
        return int(b) - int(a)
    except (ValueError, AttributeError):
        return None


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

    # 10年平均ROE（AI Berkshire: 10年平均<8%排除，我们优先用10年）
    roe_10y = stock.get('roe_10y_avg')
    roe_5y = stock.get('roe_5y_avg')
    
    # 优先用10年数据，如果10年数据不可用则降级为5年
    roe_to_check = roe_10y if roe_10y is not None else roe_5y
    
    if roe_to_check is not None and roe_to_check < 8:
        # 豁免A：战略投入期（对标 Berkshire 豁免A）——高增长 + OCF 已转正 + 数据覆盖短
        rev_g = stock.get('revenue_growth') or 0
        ocf_l = stock.get('ocf_latest')
        ocf_tr = stock.get('ocf_5y_trend', 0) or 0
        span = _data_years_span(stock.get('data_years'))
        
        # 年限判断：上游要求10年，我们用实际数据长度（但不超过10年）
        data_span = span if span is not None else 5
        effective_span = min(data_span, 10)  # 最多按10年判断
        
        # 修正：使用 ocf_per_share 作为 ocf_latest 的代理（当 ocf_latest 为空时）
        ocf_to_check = ocf_l if ocf_l is not None else stock.get('ocf_per_share')
        
        if rev_g >= 20 and ocf_to_check is not None and ocf_to_check > 0 and ocf_tr >= 0 \
                and effective_span < 10:
            reasons.append(f"{'10年' if roe_10y else '5年'}均ROE={roe_to_check}%（<8%，豁免A：高增长{rev_g}%+OCF转正+覆盖{effective_span}年投入期）")
        else:
            return []
    elif roe_to_check is not None:
        reasons.append(f"{'10年' if roe_10y else '5年'}均ROE={roe_to_check}%")

    # ── 规则 2：OCF/NI 精确计算（AI Berkshire: 5年累计FCF为负排除 + OCF/NI < 0.7排除）──
    # 代理：OCF/股正数 + 多数年份OCF为正（保持向后兼容）
    ocf = stock.get('ocf_per_share')
    ocf_pos_years = stock.get('ocf_positive_years')
    
    # 新增：OCF/NI 精确计算（如果数据可得）
    ocf_ni_ratio = None
    if stock.get('ocf_5y_sum') and stock.get('net_margin_5y_avg'):
        # OCF/NI = 5年累计OCF / 5年累计净利润（近似）
        ocf_5y_sum = stock.get('ocf_5y_sum', 0)
        net_margin_5y_avg = stock.get('net_margin_5y_avg', 0)
        if net_margin_5y_avg > 0 and ocf_5y_sum > 0:
            # 用最新年度净利润作为分母基准
            net_profit_latest = stock.get('net_profit', 0)
            if net_profit_latest > 0:
                ocf_ni_ratio = ocf_5y_sum / net_profit_latest
            else:
                # 净利润为0时用净利率推算
                ocf_ni_ratio = ocf_5y_sum / (net_margin_5y_avg * 1e8) if net_margin_5y_avg > 0 else None
    
    # 代理逻辑（保持向后兼容）- 放在精确计算之后
    if ocf is not None:
        if ocf <= 0:
            # 豁免B：战略投入期（高毛利率+高营收增长+净利已改善，缺一不可）
            gm = stock.get('gross_margin') or 0
            rev_g = stock.get('revenue_growth') or 0
            prof_g = stock.get('profit_growth') or 0
            if gm >= 30 and rev_g >= 20 and prof_g > 0:
                reasons.append(
                    f"OCF/股={ocf}（≤0，豁免：高毛利率{gm}%+高增长{rev_g}%投入期）")
            else:
                return []
        else:
            # OCF为正，再看历史多数年份是否也为正
            if ocf_pos_years is not None and ocf_pos_years < 3 and stock.get('roe_5y_count', 0) >= 4:
                return []  # 多数年份OCF为负 → 排除
            reasons.append(f"OCF/股={ocf}（正数）")
    
    # 新增：OCF/NI 精确计算检查
    if ocf_ni_ratio is not None:
        if ocf_ni_ratio < 0.7:
            # 豁免B：高毛利率+高增长+净利改善（OCF/NI < 0.7 但商业模式优秀）
            gm = stock.get('gross_margin') or 0
            rev_g = stock.get('revenue_growth') or 0
            prof_g = stock.get('profit_growth') or 0
            if gm >= 30 and rev_g >= 20 and prof_g > 0:
                reasons.append(f"OCF/NI={ocf_ni_ratio:.2f}（<0.7，豁免：高毛利率{gm}%+高增长{rev_g}%投入期）")
            else:
                reasons.append(f"OCF/NI={ocf_ni_ratio:.2f}（<0.7，不达标）")
                return []
        else:
            reasons.append(f"OCF/NI={ocf_ni_ratio:.2f}（≥0.7，通过）")
    elif ocf is not None:
        # 只有代理数据，无精确计算
        reasons.append(f"OCF/NI数据不足（仅OCF/股代理）")

    # ── 规则 3：净利率 ≥ 5%（当前 + 5年均值 + 10年均值）──
    min_nm = screen.get('min_net_margin', 5)
    nm_5y = stock.get('net_margin_5y_avg')
    nm_10y = stock.get('net_margin_10y_avg')
    
    # 优先用10年数据，如果10年数据不可用则降级为5年
    nm_to_check = nm_10y if nm_10y is not None else nm_5y
    
    if nm_to_check is not None and nm_to_check < min_nm:
        # 豁免C：主动低利润率模式（对标 Berkshire 豁免B）——毛利率>30%（有能力赚）+
        # 营收净利双正（改善趋势，非长期失血）
        gm_current = stock.get('gross_margin')
        rev_g = stock.get('revenue_growth') or 0
        prof_g = stock.get('profit_growth') or 0
        if gm_current and gm_current >= 30 and rev_g > 0 and prof_g > 0:
            reasons.append(f"{'10年' if nm_10y else '5年'}均净利率={nm_to_check}%（<{min_nm}%，豁免C：高毛利率{gm_current}%+双增长改善）")
        else:
            return []
    elif nm_to_check is not None:
        reasons.append(f"{'10年' if nm_10y else '5年'}均净利率={nm_to_check}%")

    # ── 规则 4：毛利率 ≥ 15%（当前 + 5年均值）──
    min_gm = screen.get('min_gross_margin', 15)
    gm_current = stock.get('gross_margin')
    gm_5y = stock.get('gross_margin_5y_avg')
    
    # 优先用5年均值，如果5年数据不可用则用当前值
    gm_to_check = gm_5y if gm_5y is not None else gm_current
    
    if gm_to_check is not None and gm_to_check < min_gm:
        # 豁免D：高周转薄利模式（对标 Berkshire 豁免C：Costco 类）——ROE>20% +
        # OCF 为正且过半年份为正（现金真实）+ 5年均净利率>0（不失血）
        roe = stock.get('roe') or 0
        ocf = stock.get('ocf_per_share')
        ocf_py = stock.get('ocf_positive_years')
        nm_5y_d = stock.get('net_margin_5y_avg')
        if roe >= 20 and ocf is not None and ocf > 0 \
                and ocf_py is not None and ocf_py >= 3 \
                and nm_5y_d is not None and nm_5y_d > 0:
            reasons.append(
                f"毛利率={gm_to_check}%（<{min_gm}%，豁免D：高ROE={roe}%+OCF质量+净利为正薄利模式）")
        else:
            return []
    elif gm_to_check is not None:
        reasons.append(f"毛利率={gm_to_check}%（≥{min_gm}%）")

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


# 五维基础权重（护城河 > 估值 > 增长 > 财务健康 > 定价权）
_SCORE_WEIGHTS = {'roe': 0.30, 'pe': 0.20, 'growth': 0.20,
                  'debt': 0.15, 'margin': 0.15}


def _score_breakdown(stock: dict) -> dict:
    """评分拆解（透明化基础）。

    返回与 `_calculate_moat_score` 完全一致的逐项子分 + 一致性加分 + 总分，
    供日后「评分体系透明化」详情页展示五大维度加权公式使用。
    纯函数、无副作用，不改变任何评分阈值。
    """
    weights = _SCORE_WEIGHTS
    parts: dict = {}

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
    parts['roe'] = {'raw': roe_combined, 'sub': roe_s,
                    'weight': weights['roe'],
                    'contribution': round(roe_s * weights['roe'], 2)}

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
    parts['pe'] = {'raw': pe, 'sub': pe_s, 'weight': weights['pe'],
                   'contribution': round(pe_s * weights['pe'], 2)}

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
    parts['growth'] = {'raw': avg_g, 'sub': g_s, 'weight': weights['growth'],
                       'contribution': round(g_s * weights['growth'], 2)}

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
    parts['debt'] = {'raw': debt, 'sub': d_s, 'weight': weights['debt'],
                     'contribution': round(d_s * weights['debt'], 2)}

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
    parts['margin'] = {'raw': gm_combined, 'sub': gm_s,
                       'weight': weights['margin'],
                       'contribution': round(gm_s * weights['margin'], 2)}

    # ── 6) 10年一致性/趋势加分（巴菲特风格）──
    consistency = 0
    # ROE波动性低 → 加分（稳定）
    volatility = stock.get('roe_volatility')
    if volatility is not None:
        if volatility <= 5:
            consistency += 5
        elif volatility <= 10:
            consistency += 3
        elif volatility <= 15:
            consistency += 1
    # ROE改善趋势 → 加分（5年均值 > 10年均值 = 公司在变好）
    improvement = stock.get('roe_improvement')
    if improvement is not None and improvement > 2:
        consistency += min(5, improvement)  # 最多加5分
    # FCF一致性：10年中正FCF年份多 → 加分
    fcf_pos = stock.get('fcf_positive_years_10')
    if fcf_pos is not None:
        if fcf_pos >= 8:
            consistency += 5  # 90%+年份FCF为正
        elif fcf_pos >= 6:
            consistency += 3
        elif fcf_pos >= 4:
            consistency += 1
    parts['consistency_bonus'] = consistency

    base = sum(p['contribution'] for k, p in parts.items()
               if k not in ('total', 'consistency_bonus'))
    parts['total'] = round(base + consistency, 1)
    return parts


def _calculate_moat_score(stock: dict) -> float:
    """AI Berkshire 五维评分 (0-100)。

    权重反映：护城河 > 估值 > 增长 > 财务健康 > 市场验证。
    实现委托给 `_score_breakdown`，避免评分逻辑与拆解逻辑重复漂移。
    """
    return _score_breakdown(stock)['total']


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

    def _embed_history(self, candidates: list[dict]) -> None:
        """批量加载 financial_summary 并原地嵌入每个候选（无冲突字段）。"""
        codes = [c['code'] for c in candidates]
        try:
            summaries = FinancialSummaryDAO().get_batch(codes)
        except Exception:
            summaries = {}
        for c in candidates:
            summary = summaries.get(c['code'], {})
            for k in ('roe_5y_avg', 'roe_5y_count', 'gross_margin_5y_avg',
                      'net_margin_5y_avg', 'ocf_5y_trend', 'ocf_latest',
                      'ocf_positive_years', 'debt_ratio_latest', 'data_years',
                      # 10年拓展字段
                      'roe_10y_avg', 'net_margin_10y_avg', 'fcf_10y_sum',
                      'fcf_positive_years_10', 'roe_volatility', 'roe_improvement',
                      'intcov_10y_avg', 'share_dilution_10y', 'roic_10y_avg',
                      # 新增字段：用于精确计算
                      'ocf_5y_sum', 'fcf_5y_sum', 'net_profit'):
                if summary.get(k) is not None:
                    c[k] = summary.get(k)

    def _max_n(self) -> int:
        max_n = self.cfg.get('max_candidates', 20) if isinstance(self.cfg, dict) else 20
        return max_n

    def _build_row(self, c: dict, run_id: str, run_date: str,
                   reasons: list[str], score: float,
                   strategy_tags: list[str] | None = None) -> dict:
        # 评分拆解落库（透明化前置：把五维子分+一致性加分存为 JSON，
        # 供日后详情页/Routes 展开「加权公式」使用；不影响排序分值本身）
        try:
            score_detail = json.dumps(_score_breakdown(c),
                                      ensure_ascii=False)
        except (TypeError, ValueError):
            score_detail = None

        # 数据质量标记
        data_years = c.get('data_years', '')
        has_history = bool(data_years)
        if has_history:
            reasons.append(f'财务数据覆盖={data_years}')

        return {
            'run_id': run_id,
            'run_date': run_date,
            'code': c['code'],
            'name': c['name'],
            'score': score,
            'score_detail': score_detail,
            'strategy_tags': json.dumps(strategy_tags or [], ensure_ascii=False)
            if strategy_tags is not None else None,
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
        }

    def score_candidates(self, candidates: list[dict],
                         run_id: str, run_date: str,
                         multi_strategy: bool = False,
                         strategies: dict | None = None) -> list[dict] | dict[str, list[dict]]:
        """过滤+评分+排序，返回 Top N（不写库）。

        自动从 financial_summary 加载 5年历史均值嵌入每个 stock。
        multi_strategy=False（默认）：原有单策略行为，返回 list。
        multi_strategy=True：对 strategies.yaml 每个策略单独跑一遍 7 门 +
            策略阈值 + 评分，返回 {策略名: TopN}；每行 strategy_tags 为该股
            命中的全量策略 JSON 数组。
        """
        self._embed_history(candidates)

        if not multi_strategy:
            scored = []
            for c in candidates:
                reasons = self.check_criteria(c)
                if not reasons:
                    continue
                score = self.calculate_score(c)
                scored.append(self._build_row(c, run_id, run_date, reasons, score))
            scored.sort(key=lambda x: x['score'], reverse=True)
            return scored[:self._max_n()]

        if strategies is None:
            strategies = load_strategies()
        pools: dict[str, list[dict]] = {}
        tags_by_code: dict[str, set] = {}
        for name in STRATEGY_KEYS:
            cfg = (strategies or {}).get(name)
            if not cfg:
                continue
            thresholds = cfg.get('thresholds', {})
            pool = []
            for c in candidates:
                reasons = self.check_criteria(dict(c))
                if not reasons:
                    continue
                strat_reasons = _check_strategy_thresholds(c, thresholds)
                if not strat_reasons:
                    continue
                score = self.calculate_score(c)
                pool.append(self._build_row(
                    c, run_id, run_date,
                    reasons + [f'策略{name}:{r}' for r in strat_reasons],
                    score, strategy_tags=[]))
            pool.sort(key=lambda x: x['score'], reverse=True)
            pool = pool[:self._max_n()]
            pools[name] = pool
            for row in pool:
                tags_by_code.setdefault(row['code'], set()).add(name)
        # 第二遍：每行盖上全量命中标签
        for pool in pools.values():
            for row in pool:
                row['strategy_tags'] = json.dumps(
                    sorted(tags_by_code.get(row['code'], set())),
                    ensure_ascii=False)
        return pools


def run_screener(config: dict, candidates: list[dict],
                 run_id: str = None, run_date: str = None,
                 multi_strategy: bool = False) -> list[dict]:
    """候选股评分排序 + 持久化到筛选结果表。

    multi_strategy=True 时持久化三策略池的并集（按 code 去重、保留最高分行，
    strategy_tags 为全量命中标签）。
    """
    # 注意：ValueScreener / _check_7_gates 内部会再取一层 screener.conditions，
    #      因此这里必须传完整 config，否则 config.yaml 的阈值会全部失效（走代码默认值）
    screener = ValueScreener(config)

    if run_id is None:
        run_id = now_cn().strftime("%Y%m%d_%H%M%S")
    if run_date is None:
        run_date = now_cn().strftime("%Y-%m-%d")

    scored = screener.score_candidates(candidates, run_id, run_date,
                                        multi_strategy=multi_strategy)
    if multi_strategy:
        # 三策略池并集：去重保留最高分（同分保留首见），返回 list 保持调用方兼容
        merged: dict[str, dict] = {}
        for pool in scored.values():
            for row in pool:
                prev = merged.get(row['code'])
                if prev is None or row['score'] > prev['score']:
                    merged[row['code']] = row
        top_n = sorted(merged.values(), key=lambda x: x['score'], reverse=True)
    else:
        top_n = scored

    result_dao = ScreeningResultDAO()
    if top_n:
        result_dao.save_batch(top_n)
        RunLogDAO().update_screened_count(run_id, len(top_n))

    logger.info(f"[筛选] 候选 {len(candidates)} 只 → 通过评分 {len(top_n)} 只")
    return top_n
