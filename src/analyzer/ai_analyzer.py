"""AI 选股分析模块（项目唯一的 AI 调用实现）

调用 LLM 对筛选出的股票生成：选股解析 / 投资策略 / 买卖策略。
所有脚本（run_pipeline.py / run_ai_analysis.py）都应通过本模块调用 AI，
避免多套重试策略与默认值不一致的问题。

设计要点：
- 默认使用 OpenCode Zen nemotron-3-ultra-free（免费、无 API Key 要求）
- 20 次重试应对偶发拥堵；间隔短（10s→30s）适合快速恢复的模型
- 健壮 JSON 解析（兼容 markdown 包裹 / 前后噪声文本 / 末尾逗号 / 单引号 / 截断）
- 默认 API/模型与 config.yaml 对齐，可被环境变量覆盖
"""

import json
import logging
import os
import re
import time
from typing import Optional, Callable

import httpx

logger = logging.getLogger(__name__)


# 重试策略：deepseek-v4-flash-free 是推理模型，响应约 30-60s
# 前 5 次快速探测（5→10→15→20→30s），之后固定 60s 间隔
# 共 20 次 ≈ 18 分钟最大等待
BACKOFF_SCHEDULE: list[int] = [
    5, 10, 15, 20, 30,       # 前 5 次：快速探测恢复
] + [60] * 15                # 之后：每 60s 重试一次

# 默认配置：OpenCode Zen → deepseek-v4-flash-free（免费，无需 API Key）
# 备选免费模型：nemotron-3-ultra-free（效果更好但偶有服务故障）
DEFAULT_API_BASE = "https://opencode.ai/zen/v1"
DEFAULT_MODEL = "deepseek-v4-flash-free"

# AI 分析 Prompt 模板 — 投资人笔记风格
# 不套框架、不写"视角一/二/三"、不用段永平/巴菲特/芒格名字
# 让 AI 扮演一位有经验的投资人，写出有观点、有温度的个人笔记
ANALYSIS_PROMPT = """你是一位有十年A股经验的价值投资人，正在自己的笔记本上记录对一只股票的全面思考。

这只股票的基本面如下：
- {name}（{code}）| 行业：{sector}
- PE：{pe} | PB：{pb} | ROE：{roe}% | 市值：{market_cap}亿
- 营收增长：{revenue_growth}% | 净利增长：{profit_growth}%
- 资产负债率：{debt_ratio}% | 毛利率：{gross_margin}% | 每股OCF：{ocf_per_share}
- 筛选原因：{reason}

【5年数据】
- ROE：{roe_5y}% | 毛利率：{gm_5y}% | 净利率：{nm_5y}% | OCF趋势：{ocf_trend}
- 利息覆盖：{intcov_5y}x | FCF累计：{fcf_5y}亿 | 股本稀释：{dil_5y}% | ROIC：{roic_5y}%
{fcf_yield_5y}

【10年数据】
- ROE：{roe_10y}% | 净利率：{nm_10y}% | ROIC：{roic_10y}%
- FCF累计：{fcf_10y}亿 | 10年中FCF为正：{fcf_pos_10}/10年 | 股本稀释：{dil_10y}%
- ROE标准差（波动）：{roe_vol} | ROE改善（正值=变好）：{roe_impr}
- {roe_roic_gap_line}
- FCF收益率：{fcf_yield_10y}

请输出严格的 JSON，不包含其他任何内容。JSON 结构如下：

{{
    "analysis": "你的完整笔记（分析 + 投资笔记）。用第一人称、自然语气，像在自言自语。",

    "info_richness": {{
        "grade": "A 或 B 或 C",
        "basis": "评级依据：数据覆盖年限、核心指标完整度、对这家公司的了解程度"
    }},

    "moat_evaluation": [
        {{
            "type": "转换成本",
            "score": 1-5,          /* 1=无 2=弱 3=中等 4=强 5=极强 */
            "trend": "稳定",
            "evidence": "..."
        }},
        {{
            "type": "网络效应",
            "score": 1-5,
            "trend": "稳定",
            "evidence": "..."
        }},
        {{
            "type": "无形资产（品牌/专利/许可）",
            "score": 1-5,
            "trend": "稳定",
            "evidence": "..."
        }},
        {{
            "type": "成本优势",
            "score": 1-5,
            "trend": "稳定",
            "evidence": "..."
        }},
        {{
            "type": "有效规模（自然寡头）",
            "score": 1-5,
            "trend": "稳定",
            "evidence": "..."
        }}
    ],

    "management_score": {{
        "capital_allocation": "1-10分",  /* 再投资效率+回购纪律+并购判断 */
        "shareholder_friendliness": "1-10分",  /* 稀释/回购/分红历史 */
        "summary": "1-2句话总结管理层质量"
    }},

    "intrinsic_value": {{
        "conservative": "保守估值（亿）",
        "base_case": "基准估值（亿）",
        "optimistic": "乐观估值（亿）",
        "margin_of_safety": "安全边际百分比，负值=高估",
        "method": "方法说明（如 'Owner Earnings × 10倍'）"
    }},

    "reverse_thinking": "什么情况下这家公司会死？至少2-3个真实的致死场景。如果确实想不出，就写'目前看不到明确的致命风险'。",

    "investment_strategy": "一句话说清仓位建议和持有周期",

    "trade_strategy": {{
        "signal": "BUY 或 HOLD 或 AVOID",
        "confidence": "高 或 中 或 低",
        "buy_zone": "你觉得值得买入的价格区间",
        "target_price": "目标价",
        "stop_loss": "止损条件",
        "take_profit": "止盈条件"
    }},

    "mirror_counts": "5句话中的转折词（但是/然而/不过/除非/如果/只要）计数，用逗号分隔，如 '1,0,1,0,2'。超过2个的句子在后面备注（如'2 第3句过多转折'）。",

    "mirror_test": {{
        "statements": [
            "我以___元买入___公司，因为这门生意的本质是___，我理解它",
            "它的护城河是___，而且在变宽/变窄",
            "管理层___，值得/不值得信赖",
            "当前价格相当于内在价值的___折，有/无足够安全边际",
            "即使我错了，下行风险可控/不可控，因为___"
        ],
        "passed": true/false,
        "missing": ["缺失或敷衍的句子编号（1-5）"]
    }},

    "veto_checklist": {{
        "cannot_explain_business": false,
        "negative_fcf_3y_no_improvement": false,
        "management_integrity_issue": false,
        "moat_eroding_irreversibly": false,
        "greater_fool_required": false,
        "cannot_afford_total_loss": false,
        "following_the_herd": false,
        "cannot_write_200_char_thesis": false,
        "triggered_count": 0
    }},

    "checklist": {{
        "circle_of_competence": {{"score": 1-5, "note": "一句话能否说清这门生意 + 是否真的理解"}},
        "good_business": {{"score": 1-5, "note": "经济特征综合：ROE/毛利/FCF/杠杆"}},
        "moat": {{"score": 1-5, "note": "取 moat_evaluation 的汇总判断"}},
        "management": {{"score": 1-5, "note": "取 management_score 的汇总判断"}},
        "margin_of_safety": {{"score": 1-5, "note": "当前价相对内在价值的折让"}},
        "discipline": {{"score": 1-5, "note": "仓位纪律 / 买入论述是否清晰"}}
    }}
}}

【重要指导】
1. analysis 字段写完整的投资笔记，用口语、有观点、有犹豫。不要套模板、不要分点编号、不要写"视角一/二/三"、不要提巴菲特/段永平/芒格名字。
2. moat_evaluation 中每项护城河必须给出明确的 score 和 trend。如果判断力不够，score 就打中等（3），不要勉强高分。
3. management_score 基于你能看到的数字（稀释率、ROIC趋势、资产负债率）。如果你没有确切数据下判断，就如实写"数据不足以判断"并打中等分。
4. intrinsic_value 用 Owner Earnings ≈ 最近5年平均FCF 作为基准。保守用0增长折现10倍，基准用3%增长折现12倍，乐观用5%增长折现15倍。如果FCF为负或不稳定，如实写"FCF不稳定，估值参考性有限"。
5. reverse_thinking 必须基于真实的行业/财务风险——如果是垄断国企，风险就不是"被竞争对手干掉"，而是政策风险。
6. 所有 score 字段从 1（最差）到 5 或 10（最好）。
7. info_richness 是本次分析的信息丰富度评级：A级=数据充分 长期财务齐全；B级=数据有限 部分指标需推算；C级=数据不足或上市不足3年。C级时：intrinsic_value 必须标注"数据不足，估值参考性有限"，trade_strategy.confidence 不得超过"低"。
8. checklist 六关评分（1-5★）作为汇总视图：moat 关必须与 moat_evaluation 一致，management 关必须与 management_score 一致，margin_of_safety 关必须与 intrinsic_value 一致；能力圈（circle_of_competence）和纪律（discipline）是新判断。任一关 score≤2 时，trade_strategy.signal 不应为 BUY。
9. mirror_test 是真镜子测试：用具体价格/护城河/管理层判断/估值折让/下行风险填充 5 句模板，每句都必须是具体结论而非空话。5 句缺任意一句或某一句含超限转折词 → passed=false 并在 missing 中标注句号。"5 句话说不完整 = 不买"：passed=false 时 trade_strategy.signal 不得为 BUY。
10. veto_checklist 是快速否决红线（投资纪律一票否决）：8 条逐条如实判断，任一 true 必须在该行写 true，triggered_count 填 true 的总数。8 条含义：cannot_explain_business=说不清怎么赚钱；negative_fcf_3y_no_improvement=连续3年FCF为负且无改善；management_integrity_issue=管理层诚信污点；moat_eroding_irreversibly=护城河被不可逆侵蚀；greater_fool_required=靠接盘侠赚钱（博傻）；cannot_afford_total_loss=无法承受归零；following_the_herd=因为别人都在买；cannot_write_200_char_thesis=无法用200字写清买入理由。任一 true → trade_strategy.signal 必须为 AVOID 且 confidence 不得为高。
"""


def _escape_newlines_in_strings(text: str) -> str:
    """将 JSON 字符串值中的实际换行符替换为 \\n。

    推理模型（如 deepseek-v4-flash-free）经常在 JSON 字符串值内输出
    未经转义的换行符，导致 json.loads() 失败。此函数逐字符扫描，
    仅在双引号内部替换换行符。
    """
    result = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                result.append(ch)
                escape = False
                continue
            if ch == '\\':
                result.append(ch)
                escape = True
                continue
            if ch == '"':
                in_string = False
            elif ch in '\n\r':
                # 转义未转义的换行符
                result.append('\\n')
                continue
        elif ch == '"':
            in_string = True
        result.append(ch)
    return ''.join(result)


def _complete_truncated_json(text: str) -> str:
    """补全被截断的 JSON：统计未闭合的 { [ 并补全 } ]"""
    cleaned = text.replace("'", '"')
    cleaned = re.sub(r',\s*}', '}', cleaned)
    cleaned = re.sub(r',\s*]', ']', cleaned)
    
    # 统计未闭合的大括号和中括号
    open_braces = cleaned.count('{') - cleaned.count('}')
    open_brackets = cleaned.count('[') - cleaned.count(']')
    
    # 补全
    cleaned += '}' * max(0, open_braces)
    cleaned += ']' * max(0, open_brackets)
    return cleaned


def parse_ai_response(content: str) -> Optional[dict]:
    """健壮地解析 LLM 返回的 JSON。

    兼容：markdown 代码块包裹、前后噪声文本、末尾逗号、单引号、截断补全。
    返回包含 analysis / investment_strategy / trade_strategy 的 dict，失败返回 None。
    """
    if not content:
        return None

    text = content.strip()

    # 1. 去掉 markdown 代码块包裹
    if '```json' in text:
        text = text.split('```json', 1)[1]
        text = text.split('```', 1)[0].strip()
    elif '```' in text:
        blocks = text.split('```')
        # 取最后一个以 { 开头的代码块
        extracted = None
        for b in reversed(blocks):
            b = b.strip()
            if b.startswith('{'):
                extracted = b
                break
        text = extracted if extracted else (blocks[1] if len(blocks) > 1 else text)

    # 2. 截取第一个 { 到最后一个 } 之间的内容
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        text = m.group(1)

    # 3. 直接解析
    try:
        result = json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 4. 修复常见问题：末尾逗号、单引号、截断补全
        cleaned = text.replace("'", '"')
        cleaned = re.sub(r',\s*}', '}', cleaned)
        cleaned = re.sub(r',\s*]', ']', cleaned)
        # 处理截断：如果 trade_strategy 未闭合，补全它
        if cleaned.startswith('{') and not cleaned.endswith('}'):
            # 找到最后一个完整的字段，补全剩余结构
            cleaned += '}'
        try:
            result = json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            # 5. 推理模型常在 JSON 字符串内输出未转义的实际换行符
            #    尝试将字符串内容中的实际换行替换为 \n
            try:
                cleaned = _escape_newlines_in_strings(cleaned)
                result = json.loads(cleaned, strict=False)
            except (json.JSONDecodeError, ValueError):
                # 6. 终极兜底：逐层补全嵌套结构
                cleaned = _complete_truncated_json(cleaned)
                try:
                    result = json.loads(cleaned, strict=False)
                except json.JSONDecodeError:
                    logger.error(f"[AI分析] JSON 解析失败，前200字符: {text[:200]}")
                    return None

    # 5. 校验必要字段
    required = ['analysis', 'investment_strategy', 'trade_strategy']
    if not all(k in result for k in required):
        logger.warning(f"[AI分析] 返回字段不完整: {list(result.keys())}")
        return None
    return result


# 已知的免费模型列表（无需 API Key）
# 不再硬编码——FreeModelPool 自动从 /api/models 发现
FREE_MODELS = {"nemotron-3-ultra-free", "deepseek-v4-flash-free", "mimo-v2.5-free"}

# FreeModelPool 全局单例（延迟初始化）
_model_pool: Optional['FreeModelPool'] = None


class FreeModelPool:
    """免费模型自动发现与故障轮换池。

    每 30 分钟查询 OpenCode Zen /api/models，筛选出所有 "-free" 后缀的模型，
    按偏好顺序排列。调用 acquire() 返回一个当前可用的模型，
    如果模型调用失败，调用 mark_dead() 将其暂时黑名单，自动切换到下一个。
    """

    # 偏好顺序：效果好的排前面
    PREFERRED_ORDER = [
        "deepseek-v4-flash-free",
        "mimo-v2.5-free",
        "nemotron-3-ultra-free",
    ]

    def __init__(self, api_base: str, api_key: str = None):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self._pool: list[str] = []        # 当前可用模型列表（按偏好排序）
        self._dead: set[str] = set()      # 本轮已标记为不可用的模型
        self._current = 0                 # 轮换游标
        self._last_refresh = 0.0
        self._refresh_interval = 1800     # 30 秒刷新（首次会立即刷新）
        self._logger = logging.getLogger("FreeModelPool")

    # ── 公开接口 ──

    def acquire(self) -> Optional[str]:
        """获取一个当前可用的模型。如果全部不可用，强制刷新后重试。"""
        now = time.time()
        if now - self._last_refresh > self._refresh_interval or not self._pool:
            self._refresh()

        # 在池中找一个不在黑名单的
        for _ in range(len(self._pool) + 1):
            if not self._pool:
                break
            idx = self._current % len(self._pool)
            self._current = idx + 1
            model = self._pool[idx]
            if model not in self._dead:
                return model

        # 全死了 → 强制刷新并清空黑名单
        self._logger.warning("[FreeModelPool] 全部模型已下线，强制刷新列表")
        self._dead.clear()
        self._current = 0
        self._refresh()
        return self._pool[0] if self._pool else None

    def mark_dead(self, model: str):
        """标记一个模型为不可用（本次分析周期不再尝试）。"""
        if model in self._dead:
            return
        self._dead.add(model)
        alive = len(self._pool) - len(self._dead)
        self._logger.warning(
            "[FreeModelPool] %s 下线（剩余 %d 个候选）", model, alive)

    def current_pool(self) -> list[str]:
        """返回当前模型池快照（用于日志/展示）。"""
        return list(self._pool)

    # ── 内部 ──

    def _refresh(self):
        """从 API 查询可用免费模型。"""
        self._last_refresh = time.time()
        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            url = f"{self.api_base}/models"

            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url, headers=headers)

            if resp.status_code != 200:
                self._logger.warning(
                    "[FreeModelPool] 查询模型列表 HTTP %d，使用已有缓存", resp.status_code)
                return

            data = resp.json()
            all_models = [m["id"] for m in data.get("data", [])]
            free_models = [m for m in all_models if m.endswith("-free")]

            if not free_models:
                self._logger.warning("[FreeModelPool] API 未返回任何 free 模型，保留旧缓存")
                return

            # 按偏好排序 + 保留新发现的
            ordered = []
            seen = set()
            for pref in self.PREFERRED_ORDER:
                if pref in free_models and pref not in seen:
                    ordered.append(pref)
                    seen.add(pref)
            for m in free_models:
                if m not in seen:
                    ordered.append(m)
                    seen.add(m)

            self._pool = ordered
            # 从黑名单中移除已不在池中的模型
            self._dead &= set(self._pool)
            self._current = 0
            self._logger.info(
                "[FreeModelPool] 发现 %d 个免费模型: %s",
                len(self._pool), ", ".join(self._pool))

        except Exception as e:
            self._logger.warning(
                "[FreeModelPool] 刷新失败: %s，使用已有缓存", e)

    def __repr__(self):
        alive = len(self._pool) - len(self._dead)
        return f"FreeModelPool({alive}/{len(self._pool)} alive)"


def get_model_pool(api_base: str, api_key: str = None) -> FreeModelPool:
    """获取 FreeModelPool 全局单例。"""
    global _model_pool
    if _model_pool is None:
        _model_pool = FreeModelPool(api_base, api_key)
    return _model_pool


class AiAnalyzer:
    """AI 选股分析器（项目唯一实现）。"""

    def __init__(self, config: dict = None):
        """
        Args:
            config: config.yaml 中 ai 段，形如::

                {
                    'api_base': 'https://opencode.ai/zen/v1',
                    'model': 'deepseek-v4-flash-free',
                    'temperature': 0.3,
                    'max_tokens': 6000,
                }

            环境变量优先于 config：STOCK_AI_API_KEY / STOCK_AI_API_BASE / STOCK_AI_MODEL
        """
        cfg = config or {}
        self.api_base = (
            os.getenv('STOCK_AI_API_BASE')
            or cfg.get('api_base')
            or DEFAULT_API_BASE
        ).rstrip('/')
        # 允许 .env 里已包含 /chat/completions
        if self.api_base.endswith('/chat/completions'):
            self.api_base = self.api_base[:-len('/chat/completions')]
        self.model = os.getenv('STOCK_AI_MODEL') or cfg.get('model') or DEFAULT_MODEL
        self.temperature = cfg.get('temperature', 0.3)
        self.max_tokens = cfg.get('max_tokens', 6000)

        self.api_key = os.getenv('STOCK_AI_API_KEY') or os.getenv('OPENAI_API_KEY')
        self._is_free_model = self.model.endswith('-free')
        # 免费模型自动启用模型池（自动发现 + 故障轮换）
        if self._is_free_model:
            self._pool = get_model_pool(self.api_base, self.api_key)
        else:
            self._pool = None
        if not self.api_key and not self._is_free_model:
            logger.warning(
                "[AI分析] 未设置 API Key（STOCK_AI_API_KEY / OPENAI_API_KEY），"
                "且模型 %s 不是已知免费模型", self.model
            )
        # 最近一次调用的 token 用量（由 _call_llm 写入，供 analyze_batch 记录日志）
        self._last_usage: Optional[dict] = None
        # 批内固定模型：一批分析（analyze_batch 一次调用）复用同一模型，失败才轮换
        self._batch_model: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or self._is_free_model

    def analyze_stock(self, stock: dict) -> Optional[dict]:
        """对一只股票进行 AI 分析，返回解析后的 dict（失败返回 None）。"""
        if not self.api_key and not self._is_free_model:
            logger.error("[AI分析] 无 API Key，跳过分析")
            return None

        # 计算派生指标
        mc = stock.get('market_cap', 'N/A')
        mc_float = float(mc) if mc not in (None, 'N/A') else None
        fcf_5y = stock.get('fcf_5y_sum')
        fcf_10y = stock.get('fcf_10y_sum')
        roe_5y = stock.get('roe_5y_avg')
        roic_5y = stock.get('roic_5y_avg')

        # FCF 收益率行
        fcf_yield_5y = ''
        if fcf_5y and mc_float and mc_float > 0:
            annual_fcf = fcf_5y / 5
            yield_pct = annual_fcf / mc_float * 100
            fcf_yield_5y = f'年均FCF/市值: {yield_pct:.1f}%'
        # 10年FCF收益率
        fcf_yield_10y = 'N/A'
        if fcf_10y and mc_float and mc_float > 0:
            annual_fcf_10 = fcf_10y / 10
            yield_pct_10 = annual_fcf_10 / mc_float * 100
            fcf_yield_10y = f'{yield_pct_10:.1f}%'
        # ROE - ROIC 差距
        roe_roic_gap_line = ''
        if roe_5y is not None and roic_5y is not None and roic_5y > 0:
            gap = roe_5y - roic_5y
            if gap > 5:
                roe_roic_gap_line = f'[注意] ROE比ROIC高{gap:.1f}%（可能靠杠杆撑ROE）'
            elif gap > 2:
                roe_roic_gap_line = f'ROE比ROIC高{gap:.1f}%'
            else:
                roe_roic_gap_line = f'ROE与ROIC差距{gap:.1f}%（合理）'

        prompt = ANALYSIS_PROMPT.format(
            name=stock.get('name', ''),
            code=stock.get('code', ''),
            pe=stock.get('pe', 'N/A'),
            pb=stock.get('pb', 'N/A'),
            roe=stock.get('roe', 'N/A'),
            revenue_growth=stock.get('revenue_growth', 'N/A'),
            profit_growth=stock.get('profit_growth', 'N/A'),
            debt_ratio=stock.get('debt_ratio', 'N/A'),
            market_cap=stock.get('market_cap', 'N/A'),
            sector=stock.get('sector', '未知'),
            gross_margin=stock.get('gross_margin', 'N/A'),
            ocf_per_share=stock.get('ocf_per_share', 'N/A'),
            reason=stock.get('reason', ''),
            # 5年均值
            roe_5y=stock.get('roe_5y_avg', 'N/A'),
            gm_5y=stock.get('gross_margin_5y_avg', 'N/A'),
            nm_5y=stock.get('net_margin_5y_avg', 'N/A'),
            ocf_trend={0:'波动',1:'增长',-1:'下降'}.get(stock.get('ocf_5y_trend'), '未知'),
            intcov_5y=stock.get('intcov_5y_avg', 'N/A'),
            fcf_5y=f'{fcf_5y/1e8:.1f}' if fcf_5y else 'N/A',
            dil_5y=stock.get('share_dilution_5y', 'N/A'),
            roic_5y=stock.get('roic_5y_avg', 'N/A'),
            fcf_yield_5y=fcf_yield_5y,
            # 10年均值
            roe_10y=stock.get('roe_10y_avg', 'N/A'),
            nm_10y=stock.get('net_margin_10y_avg', 'N/A'),
            roic_10y=stock.get('roic_10y_avg', 'N/A'),
            fcf_10y=f'{fcf_10y/1e8:.1f}' if fcf_10y else 'N/A',
            fcf_pos_10=stock.get('fcf_positive_years_10', 'N/A'),
            dil_10y=stock.get('share_dilution_10y', 'N/A'),
            roe_vol=stock.get('roe_volatility', 'N/A'),
            roe_impr=stock.get('roe_improvement', 'N/A'),
            roe_roic_gap_line=roe_roic_gap_line,
            fcf_yield_10y=fcf_yield_10y,
        )
        # 注入历史分析摘要（仅当有历史记录时追加）
        history_summary = _build_history_summary(stock.get('code', ''))
        if history_summary:
            prompt += (
                "\n\n" + history_summary +
                "\n\n在本次分析中，关注以下问题："
                "\n1. 你过往的判断是否仍然成立？哪些条件变了？"
                "\n2. 过去哪次判断被市场验证了、哪次被打脸了？"
                "\n3. 本次结论相比之前是否有转变？为什么？"
            )

        content, used_model, usage = self._call_llm(prompt)
        self._last_usage = usage  # 供调用方写入 ai_analysis_log
        if not content:
            return None
        logger.info(f"[AI分析] 模型=[{used_model}] {stock.get('code')} {stock.get('name')}")
        result = parse_ai_response(content)
        if result is not None and isinstance(result, dict):
            result['model'] = used_model
        return result

    # ── 模型调用（含故障轮换） ──

    def _resolve_model(self) -> str:
        """返回本次应使用的模型名称。免费模型从池中获取，非免费用配置值。

        批内固定：一批分析首次取到的模型会被固定复用（_batch_model），
        直到该模型被标记死亡（_mark_model_dead 会清空 _batch_model），
        之后重新 acquire 换下一个——保证单批 20 只质量一致，批间自动轮换。
        """
        if self._pool:
            if self._batch_model and self._batch_model not in self._pool._dead:
                return self._batch_model
            model = self._pool.acquire()
            if model:
                self._batch_model = model
                return model
            logger.warning("[AI分析] 模型池无可用模型，回退到配置值: %s", self.model)
        return self.model

    def _mark_model_dead(self, model: str):
        """标记模型不可用（仅对池中的免费模型生效）。"""
        if self._pool:
            self._pool.mark_dead(model)
            # 批内固定模型死亡 → 解除固定，下次 _resolve_model 重新 acquire
            if self._batch_model == model:
                self._batch_model = None

    def _call_llm(self, prompt: str) -> tuple[Optional[str], Optional[str], Optional[dict]]:
        """调用 LLM API，使用 BACKOFF_SCHEDULE 做 20 次退避重试。

        返回 (content, used_model, usage)，失败时 content/usage 为 None。
        usage 形如 {'prompt_tokens': int, 'completion_tokens': int, 'model': str}。
        当 enable_pool=True 时，模型会在连续故障时自动轮换。
        """
        headers = {
            'Content-Type': 'application/json',
        }
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        # 当前的模型（可能会在轮换中改变）
        current_model = self._resolve_model()
        url = f"{self.api_base}/chat/completions"
        max_retries = len(BACKOFF_SCHEDULE)
        consecutive_timeouts = 0

        for attempt in range(max_retries):
            payload = {
                'model': current_model,
                'messages': [
                    {'role': 'system',
                     'content': '你只输出JSON，不输出其他任何内容。必须严格按照用户指定的JSON结构输出。'},
                    {'role': 'user', 'content': prompt},
                ],
                'temperature': self.temperature,
                'max_tokens': self.max_tokens,
            }

            try:
                with httpx.Client(timeout=240.0) as client:
                    resp = client.post(url, headers=headers, json=payload)

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        content = data['choices'][0]['message']['content']
                        _usage_raw = data.get('usage') or {}
                    except (KeyError, IndexError, ValueError) as e:
                        logger.warning("[AI分析] 200但响应结构异常: %s (%s), 轮换模型", e, current_model)
                        self._mark_model_dead(current_model)
                        current_model = self._resolve_model()
                        consecutive_timeouts = 0
                        time.sleep(2)
                        continue
                    if not content or len(content) < 10:
                        logger.warning(
                            "[AI分析] 响应内容过短(%s字符, %s), 轮换模型重试...",
                            len(content), current_model)
                        self._mark_model_dead(current_model)
                        current_model = self._resolve_model()
                        consecutive_timeouts = 0
                        continue
                    # 提取 token 用量（用于写入 ai_analysis_log）
                    try:
                        usage = {
                            'prompt_tokens': int(_usage_raw.get('prompt_tokens', 0) or 0),
                            'completion_tokens': int(_usage_raw.get('completion_tokens', 0) or 0),
                            'model': current_model,
                        }
                    except Exception:
                        usage = None
                    return content, current_model, usage

                # ── 非 200 状态码 ──
                should_rotate = self._is_fatal_model_error(resp)
                if should_rotate:
                    self._mark_model_dead(current_model)
                    current_model = self._resolve_model()
                    logger.warning(
                        "[AI分析] HTTP %d (%s), 已轮换至新模型", resp.status_code, current_model)
                elif resp.status_code == 429:
                    self._log_rate_limit(resp, attempt, max_retries)
                else:
                    logger.warning(
                        "[AI分析] HTTP %d, 退避 %ds (%s)",
                        resp.status_code, BACKOFF_SCHEDULE[attempt], current_model)
                time.sleep(BACKOFF_SCHEDULE[attempt])

            except httpx.TimeoutException:
                consecutive_timeouts += 1
                logger.warning(
                    "[AI分析] 超时(%s) 第%d次, 退避 %ds",
                    current_model, consecutive_timeouts, BACKOFF_SCHEDULE[attempt])
                if consecutive_timeouts >= 2:
                    logger.warning("[AI分析] 连续超时 %d 次, 轮换模型", consecutive_timeouts)
                    self._mark_model_dead(current_model)
                    current_model = self._resolve_model()
                    consecutive_timeouts = 0
                    time.sleep(2)
                else:
                    time.sleep(BACKOFF_SCHEDULE[attempt])
            except httpx.RequestError as e:
                logger.warning(
                    "[AI分析] 网络错误(%s): %s, 退避 %ds",
                    current_model, e, BACKOFF_SCHEDULE[attempt])
                time.sleep(BACKOFF_SCHEDULE[attempt])
            except Exception as e:
                logger.warning("[AI分析] 异常(%s): %s, 退避 %ds",
                               current_model, e, BACKOFF_SCHEDULE[attempt])
                time.sleep(BACKOFF_SCHEDULE[attempt])

        logger.error("[AI分析] 已达最大重试次数 %s，放弃 (末模型=%s)", max_retries, current_model)
        return None, current_model, None

    @staticmethod
    def _is_fatal_model_error(resp: httpx.Response) -> bool:
        """判断 HTTP 响应是否表示该模型已失效，应轮换到下一个。

        触发轮换的条件：
        - 500/503 Internal Server Error（模型服务端故障）
        - 返回的 error.message 包含 "from provider"、"upstream"、"rate-limited"
        - 返回模型已知已下线错误代码
        """
        if resp.status_code in (500, 502, 503):
            return True
        if resp.status_code >= 400:
            try:
                err_msg = str(resp.json().get('error', {}))
                fatal_keywords = ['from provider', 'upstream', 'rate-limited',
                                  'temporarily', 'not available', 'free is temporarily']
                for kw in fatal_keywords:
                    if kw in err_msg.lower():
                        return True
            except Exception:
                pass
        return False

    @staticmethod
    def _log_rate_limit(resp: httpx.Response, attempt: int, max_retries: int):
        try:
            err = resp.json().get('error', {})
            err_code = err.get('code', '')
            if err_code == '1305':
                logger.warning(
                    f"[AI分析] 拥挤1305({err.get('message','')}) 退避 {attempt+1}/{max_retries}")
                return
        except Exception:
            pass
        logger.warning(
            f"[AI分析] 限流(429), 退避 {BACKOFF_SCHEDULE[attempt]}s ({attempt+1}/{max_retries})")


# ── 批量分析 ──

def _build_history_summary(stock_code: str, limit: int = 3) -> str:
    """从 stock_analysis_history 提取历史关键行摘要。
    
    每条压缩为一行：日期 + Signal(置信度) | 核心判断第一句
    目标 ~150 tokens，避免 prompt 膨胀。
    """
    import json
    from src.models.database import StockAnalysisHistoryDAO

    records = StockAnalysisHistoryDAO().get_history(stock_code, limit=limit)
    if not records:
        return ""

    lines = []
    for r in records:
        date = (r.get('analysis_date') or '')[:10]
        trade_str = r.get('ai_trade_strategy', '{}')
        try:
            trade = json.loads(trade_str) if isinstance(trade_str, str) else trade_str
        except (json.JSONDecodeError, TypeError):
            trade = {}
        signal = trade.get('signal', '--')
        confidence = trade.get('confidence', '')

        # 从 ai_analysis 提取第一句作为核心判断
        analysis = r.get('ai_analysis', '')
        core = ''
        try:
            parsed = json.loads(analysis) if isinstance(analysis, str) else analysis
            if isinstance(parsed, dict):
                text = str(parsed.get('analysis', ''))
            else:
                text = str(analysis)
            core = text.strip().split('\n')[0][:80] if text else ''
        except (json.JSONDecodeError, TypeError, IndexError):
            pass

        conf_str = f"({confidence})" if confidence else ""
        lines.append(f"  {date} {signal}{conf_str} | {core}")

    if not lines:
        return ""
    return "【过往分析记录】\n" + "\n".join(lines)


def _save_analysis(stock: dict, result: dict, run_id: str):
    """把 AI 分析结果写入 screening_result 与 stock_analysis_history。"""
    from src.models.database import ScreeningResultDAO, StockAnalysisHistoryDAO

    analysis_json = json.dumps(result, ensure_ascii=False)
    strategy = result.get('investment_strategy', '')
    if isinstance(strategy, dict):
        strategy = json.dumps(strategy, ensure_ascii=False)
    trade = result.get('trade_strategy', {})
    trade_json = json.dumps(trade, ensure_ascii=False) if isinstance(trade, dict) else str(trade)

    ScreeningResultDAO().update_ai_analysis(
        run_id, stock['code'], analysis_json, strategy, trade_json)
    StockAnalysisHistoryDAO().save(
        stock['code'], run_id, stock.get('score'),
        analysis_json, trade_json)


def _save_failure(stock: dict, run_id: str):
    """记录一次失败尝试（写入空记录，避免短时间内重复尝试）。"""
    from src.models.database import StockAnalysisHistoryDAO
    StockAnalysisHistoryDAO().save(
        stock['code'], run_id, stock.get('score'), '{}', '{}')


def analyze_batch(stocks: list[dict], run_id: str,
                  interval_seconds: int = 60,
                  on_progress: Optional[Callable[[int, int, int], None]] = None
                  ) -> tuple[int, int]:
    """批量分析股票并持久化结果。

    Args:
        stocks: 待分析股票列表（需含 code/name/score 等字段）
        run_id: 运行批次 ID
        interval_seconds: 每只股票之间的冷却间隔（默认 60s，避免限流）
        on_progress: 进度回调 (analyzed_ok, analyzed_failed, current_index)

    Returns:
        (成功数, 失败数)
    """
    from src.config import load_config
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    total = len(stocks)
    if total == 0:
        return 0, 0
    if not analyzer.configured:
        logger.error("[AI分析] 无 API Key，跳过全部")
        for i, s in enumerate(stocks):
            _save_failure(s, run_id)
        return 0, total

    logger.info(f"[AI分析] 开始分析 {total} 只股票...")
    analyzed_ok = 0
    analyzed_failed = 0

    from src.models.database import AiAnalysisLogDAO
    log_dao = AiAnalysisLogDAO()

    for idx, stock in enumerate(stocks):
        code, name = stock.get('code'), stock.get('name')
        logger.info(f"[AI分析] ({idx+1}/{total}) {code} {name}...")
        if on_progress:
            on_progress(analyzed_ok, analyzed_failed, idx)

        result = analyzer.analyze_stock(stock)

        # 单只失败：间隔 30s 重试 1 次（_call_llm 内部已有 20 次退避+模型轮换，
        # 这里仅做一次外层兜底，避免单只股票的偶发 prompt/解析问题拖累整批）
        if result is None and idx < total - 1:
            logger.info(f"[AI分析] {code} 首次失败，30s 后重试 1 次...")
            time.sleep(30)
            result = analyzer.analyze_stock(stock)

        if result:
            _save_analysis(stock, result, run_id)
            # 写入 token 用量日志（免费模型 cost=0）
            try:
                usage = getattr(analyzer, '_last_usage', None) or {}
                log_dao.log(
                    run_id, code,
                    usage.get('model', analyzer.model),
                    usage.get('prompt_tokens', 0),
                    usage.get('completion_tokens', 0),
                    0.0,
                )
            except Exception as e:
                logger.debug(f"[AI分析] 写 ai_analysis_log 失败（不影响主流程）: {e}")
            analyzed_ok += 1
            logger.info(f"  {analyzed_ok}/{total} 完成")
        else:
            _save_failure(stock, run_id)
            analyzed_failed += 1
            logger.warning(f"  失败 {analyzed_failed}")

        # 股票之间冷却
        if idx < total - 1:
            time.sleep(interval_seconds)

    if on_progress:
        on_progress(analyzed_ok, analyzed_failed, total)
    logger.info(
        f"[AI分析] 完成: 成功 {analyzed_ok}/{total}（失败 {analyzed_failed}）")
    return analyzed_ok, analyzed_failed
