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
import math
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

    "verdict": "最终结论三态，只能填 通过 或 不通过 或 灰色地带，并用一句话写理由，格式如 '通过：护城河宽且估值有折让'。灰色地带=看不懂或证据不足，纪律是不买。",
    "price_tiers": {{
        "aggressive": {{"advice": "激进型建议", "range": "价格区间，如 95-105"}},
        "moderate": {{"advice": "稳健型建议", "range": "价格区间"}},
        "conservative": {{"advice": "保守型建议", "range": "价格区间或观望"}}
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

    "thesis": {{
        "core_thesis": "论点一句话（200字内）：我以___元买入___，因为…",
        "assumptions": [
            {{"content": "可验证假设", "verify_method": "验证方式", "verify_freq": "季度/半年/事件", "status": "未验证"}}
        ],
        "red_lines": [
            {{"condition": "触发条件", "action": "触发后动作（重新评估/清仓）"}}
        ],
        "sell_conditions": ["买入前写下的卖出条件1"]
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
4. intrinsic_value 估值方法自选（禁用固定倍数）：先看商业模式再选方法——稳定现金牛用 Owner Earnings 折现；周期/困境用重置成本或清算视角并明说局限；高研发科技若 FCF 失真，改用 PE/PB 相对估值或直接写"不适用精确估值"；股息稳定可用股息折现。三档（保守/基准/乐观）是"假设不同"不是"倍数不同"：每档必须写清核心假设（增长率/折现率/利润率）。method 栏写真实方法名 + 一句话局限。如果 FCF 为负或不稳定、或关键字段缺失，如实写"数据不足，估值参考性有限"，不得硬算。
5. reverse_thinking 必须基于真实的行业/财务风险——如果是垄断国企，风险就不是"被竞争对手干掉"，而是政策风险。
6. 所有 score 字段从 1（最差）到 5 或 10（最好）。
7. info_richness 是本次分析的信息丰富度评级：A级=数据充分 长期财务齐全；B级=数据有限 部分指标需推算；C级=数据不足或上市不足3年。C级时：intrinsic_value 必须标注"数据不足，估值参考性有限"，trade_strategy.confidence 不得超过"低"。
8. checklist 六关评分（1-5★）作为汇总视图：moat 关必须与 moat_evaluation 一致，management 关必须与 management_score 一致，margin_of_safety 关必须与 intrinsic_value 一致；能力圈（circle_of_competence）和纪律（discipline）是新判断。任一关 score≤2 时如仍给 BUY，必须在 verdict 理由句中解释为何低分仍值得买（否则视为无效分析）。
9. mirror_test 是真镜子测试：用具体价格/护城河/管理层判断/估值折让/下行风险填充 5 句模板，每句都必须是具体结论而非空话。5 句缺任意一句在 missing 中标注句号。passed=false 时如仍给 BUY，必须在 verdict 理由句中解释（否则视为无效分析）；系统不再自动改你的信号，但会标记人工复核。
10. veto_checklist 是快速否决红线（投资纪律一票否决）：8 条逐条如实判断，任一 true 必须在该行写 true，triggered_count 填 true 的总数。8 条含义：cannot_explain_business=说不清怎么赚钱；negative_fcf_3y_no_improvement=连续3年FCF为负且无改善；management_integrity_issue=管理层诚信污点；moat_eroding_irreversibly=护城河被不可逆侵蚀；greater_fool_required=靠接盘侠赚钱（博傻）；cannot_afford_total_loss=无法承受归零；following_the_herd=因为别人都在买；cannot_write_200_char_thesis=无法用200字写清买入理由。任一 true → trade_strategy.signal 必须为 AVOID 且 confidence 不得为高。
11. verdict 是强制结论：不通过 → trade_strategy.signal 必须为 AVOID；灰色地带 → signal 不得为 BUY（最多 HOLD）；signal 为 BUY 时 verdict 必须为通过。灰色地带就是"不买"的纪律，不要在灰色时给买入建议。price_tiers 三档都要填（拿不准就写观望及理由，不许空着）。
12. 做A级（信息充裕）分析时必须反面检验：共识过强的股票，你的输出会趋同于市场定价。自问三件事——我的确定性来自生意本质还是资料数量？资料量减半结论会变吗？与市场共识雷同处，我的信息优势在哪？findings 里必须写一段"聪明人为什么不买"的反方论据（找不到才允许写"未找到有力反方论据"）。
13. thesis 是投资论点本体，必须含：论点一句话（200字内）、3-7条可验证假设（每条写明假设内容+验证方式+验证频率）、红线清单（触发后立即重新评估的条件）、买入前写下的卖出条件。论点失效=假设被证伪，假设未被证伪但价格透支=仍可持有。
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


def _normalize_verdict(v) -> str:
    """归一化 verdict：'pass' / 'fail' / 'gray' / ''（缺失或无法识别）。
    注意'不通过'含'通过'二字，必须先判 fail。"""
    if not v or not isinstance(v, str):
        return ''
    t = v.strip()
    tl = t.lower()
    if t.startswith('不通过') or '不买' in t or 'fail' in tl:
        return 'fail'
    if t.startswith('灰色') or 'gray' in tl or 'grey' in tl or '观望' in t:
        return 'gray'
    if t.startswith('通过') or tl.startswith('pass'):
        return 'pass'
    return ''


def _check_output_consistency(result: dict) -> list[tuple]:
    """确定性交叉检测：否决触发/镜子未过/六关≤2 却给 BUY/通过 → 返回 [(code, 矛盾描述)]。

    code ∈ veto（硬）/ mirror / checklist（软 tension，仅警告记账）。
    空列表 = 自洽。纯函数，可单测；写库前用于质量记账与警告。
    分析师整改（2026-09-21）：mirror/六关不再视为"不一致"错误，只是需要解释的 tension。
    """
    issues = []
    if not isinstance(result, dict):
        return issues
    trade = result.get('trade_strategy')
    sig = str(trade.get('signal', '')).upper() if isinstance(trade, dict) else ''
    veto = result.get('veto_checklist') or {}
    trig = 0
    try:
        trig = int(veto.get('triggered_count', 0) or 0)
    except (ValueError, TypeError):
        trig = 0
    true_items = [k for k, v in veto.items()
                  if k != 'triggered_count' and v is True]
    if trig > 0 or true_items:
        if _normalize_verdict(result.get('verdict')) == 'pass':
            issues.append(('veto', '否决触发却判通过'))
        if sig == 'BUY':
            issues.append(('veto', '否决触发却给 BUY'))
    mirror = result.get('mirror_test') or {}
    if mirror.get('passed') is False and sig == 'BUY':
        issues.append(('mirror', '镜子未过却给 BUY（需 verdict 理由解释）'))
    checklist = result.get('checklist') or {}
    low = [k for k, v in checklist.items()
           if isinstance(v, dict) and isinstance(v.get('score'), (int, float))
           and v['score'] <= 2]
    if low and sig == 'BUY':
        issues.append(('checklist', f"六关{','.join(low)}≤2 却给 BUY（需 verdict 理由解释）"))
    return issues


# ---------------------------------------------------------------------------
# C3 数字抽检（warn-only）：AI 正文引用的 ROE/PE/估值数字是否与库一致
# ---------------------------------------------------------------------------

# 标签 → stock 字段（按优先序；命中任一即自洽，未命中记首字段为参照）
_NUMERIC_LABEL_FIELDS = [
    (('ROE', '净资产收益率'), ('roe_5y_avg', 'roe')),
    (('PE', '市盈率'), ('pe',)),
    (('PB', '市净率'), ('pb',)),
    (('毛利率',), ('gross_margin_5y_avg', 'gross_margin')),
    (('净利率',), ('net_margin_5y_avg', 'net_margin')),
    (('ROIC',), ('roic_5y_avg',)),
    (('资产负债率', '负债率'), ('debt_ratio',)),
    (('市值',), ('market_cap',)),
    (('股本稀释', '股本扩张'), ('share_dilution_5y',)),
    (('利息覆盖', '利息保障'), ('intcov_5y_avg',)),
]

# 数字形态：可选符号位 + 千分位逗号/中文逗号/小数点
_NUM = r'[+\-−–－＋]?[\d,，.]+'


def _clean_cited_num(s: str):
    """带逗号/中文逗号/各类正负号的数字串转 float，失败回 None。"""
    s = s.replace(',', '').replace('，', '').strip()
    for ch in ('−', '–', '－'):
        s = s.replace(ch, '-')
    s = s.replace('＋', '+')
    try:
        return float(s)
    except ValueError:
        return None


def _ref_float(stock: dict, field: str):
    """库参照值转 float；None/'N/A'/非数字 → None（跳过比对，不记账）。"""
    v = stock.get(field)
    if v is None:
        return None
    if isinstance(v, str):
        if v.strip().upper() in ('N/A', 'NA', '-', ''):
            return None
        try:
            return float(v.replace(',', '').strip())
        except ValueError:
            return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _check_numeric_citations(analysis_text, stock: dict) -> list:
    """抽检 AI 正文带标签数字与库参照是否一致。

    只认"标签+数字"（如 ROE 15.2%、PE 28x、市值 2000亿），
    无标签裸数字无法归因，直接忽略，避免误报。
    math.isclose(rel_tol=0.02, abs_tol=0.1)：小数舍入差异放行，
    实质偏离才记 mismatch。返回 mismatch 列表（空=干净）。
    纯函数，可单测；调用方 warn-only，永不阻断。
    """
    if not analysis_text or not isinstance(analysis_text, str):
        return []
    if not isinstance(stock, dict):
        return []
    mismatches = []
    for labels, fields in _NUMERIC_LABEL_FIELDS:
        refs = [(_ref_float(stock, f)) for f in fields]
        refs = [r for r in refs if r is not None]
        if not refs:
            continue
        label_alt = '(?:' + '|'.join(re.escape(l) for l in labels) + ')'
        # 标签与数字之间允许 ≤8 个非数字字符（高达/达到/约/：等），
        # 排除换行与表格竖线，避免跨单元格误归因
        pat = re.compile(
            label_alt + r'[^\d\n|]{0,8}?(' + _NUM + r')'
            r'\s*(%|％|[xX倍]|亿)?'
        )
        for m in pat.finditer(analysis_text):
            cited = _clean_cited_num(m.group(1))
            if cited is None:
                continue
            if any(math.isclose(cited, r, rel_tol=0.02, abs_tol=0.1)
                   for r in refs):
                continue
            mismatches.append({
                'label': m.group(0).strip()[:24],
                'field': fields[0],
                'cited': cited,
                'reference': refs[0],
            })
    return mismatches


def _enforce_verdict_discipline(result: dict) -> dict:
    """verdict↔signal 程序纪律 + 否决硬执行（写库前执行）。
    否决触发 → verdict 不通过 + signal AVOID；灰色（含缺失）→ BUY 降 HOLD。
    镜子未过/六关≤2 → 不再自动熔断（2026-09-21 分析师整改：分数型规则易诱发
    调分博弈，且会排除反直觉深度判断），只记警告供人工复核，信号原样保留。
    只收紧不放松（veto/verdict 部分），改动记 _discipline_note。"""
    if not isinstance(result, dict):
        return result
    trade = result.get('trade_strategy')
    if not isinstance(trade, dict):
        return result
    notes = []
    sig = str(trade.get('signal', '')).upper()
    # 1. 否决触发（规则 10 程序化）：结论改判 + 信号改 AVOID
    veto = result.get('veto_checklist') or {}
    try:
        trig = int(veto.get('triggered_count', 0) or 0)
    except (ValueError, TypeError):
        trig = 0
    if trig > 0 or any(k != 'triggered_count' and v is True
                       for k, v in veto.items()):
        if _normalize_verdict(result.get('verdict')) != 'fail':
            result['verdict'] = '不通过：快速否决红线触发（程序强制）'
            notes.append('否决触发，verdict 改判不通过')
        if sig != 'AVOID':
            trade['signal'] = 'AVOID'
            notes.append('否决触发，signal 强制为 AVOID')
        sig = 'AVOID'
    # 2. verdict↔signal（B2 原逻辑）
    v = _normalize_verdict(result.get('verdict'))
    if not v:
        v = 'gray'
        result['verdict'] = (result.get('verdict') or '') + '灰色地带：模型未输出结论，按纪律保守处置' \
            if result.get('verdict') else '灰色地带：模型未输出结论，按纪律保守处置'
    if v == 'fail' and sig != 'AVOID':
        trade['signal'] = 'AVOID'
        notes.append('verdict 不通过，signal 强制为 AVOID')
        sig = 'AVOID'
    elif v == 'gray' and sig == 'BUY':
        trade['signal'] = 'HOLD'
        notes.append('verdict 灰色，BUY 降为 HOLD')
        sig = 'HOLD'
    # 3. 镜子/六关 tension 警告记账（不改信号，见上）
    if sig == 'BUY':
        mirror = result.get('mirror_test') or {}
        checklist = result.get('checklist') or {}
        low = [k for k, v in checklist.items()
               if isinstance(v, dict) and isinstance(v.get('score'), (int, float))
               and v['score'] <= 2]
        if mirror.get('passed') is False:
            notes.append('镜子未过但给 BUY（需人工复核，未改信号）')
        elif low:
            notes.append(f"六关{','.join(low)}≤2 但给 BUY（需人工复核，未改信号）")
    if notes:
        result['_discipline_note'] = '; '.join(notes)
    return result


def _enforce_intrinsic_discipline(result: dict, stock: dict) -> dict:
    """终值验算纪律（C1）：写库前检查 intrinsic_value 三档是否通过 C1/C2 体检。

    - C1：乐观档隐含 g > 2% → 该档标注"分母失效，仅情景参考"
    - C2：任一档 r-g < 5pct → 该档标注"分母失效，仅情景参考"
    不阻断落库，只改判标注（估值是观点，验算是标尺）。
    """
    if not isinstance(result, dict):
        return result

    intrinsic = result.get('intrinsic_value')
    if not isinstance(intrinsic, dict):
        return result

    # 需要 stock 的财务数据来跑验算
    roic_5y = stock.get('roic_5y_avg')
    fcf_5y = stock.get('fcf_5y_sum')
    market_cap_yi = stock.get('market_cap')
    current_price = stock.get('current_price')

    if roic_5y is None or fcf_5y is None or market_cap_yi is None or current_price is None:
        return result

    from decimal import Decimal
    from scripts.verify_intrinsic import (
        exact, gordon_terminal_pe, implied_g_from_multiple,
        C1_G_CAP_CNY, C2_DENOM_MIN, R_CNY_MEDIAN,
    )

    roic = exact(roic_5y) / Decimal('100')  # % 转小数
    fcf_5y_dec = exact(fcf_5y)
    if fcf_5y_dec <= 0:
        return result  # FCF 非正，无法算 Owner Earnings
    oe_annual_yi = fcf_5y_dec / Decimal('5') / Decimal('1e8')  # 年均 FCF (亿)

    tiers = ['pessimistic', 'base_case', 'optimistic']
    g_values = {
        'pessimistic': Decimal('0'),
        'base_case': Decimal('0.03'),
        'optimistic': Decimal('0.04'),
    }

    notes = []
    for tier in tiers:
        iv_str = intrinsic.get(tier)
        if iv_str is None or oe_annual_yi <= 0:
            continue

        iv_yi = exact(iv_str)
        implied_multiple = iv_yi / oe_annual_yi

        # C2 体检：终值 PE 分母检查
        g = g_values[tier]
        term_res = gordon_terminal_pe(roic, g, R_CNY_MEDIAN)
        c2_fail = not term_res['denom_ok']

        # C1 体检：反解隐含 g
        implied_g = implied_g_from_multiple(implied_multiple, roic, R_CNY_MEDIAN)
        c1_fail = implied_g is not None and implied_g > C1_G_CAP_CNY

        if c1_fail or c2_fail:
            # 标注该档估值
            tier_data = intrinsic[tier]
            if isinstance(tier_data, dict):
                orig_advice = tier_data.get('advice', '')
            else:
                orig_advice = str(tier_data)
            suffix = []
            if c1_fail:
                suffix.append(f'隐含g={float(implied_g)*100:.1f}%超2%上限')
            if c2_fail:
                suffix.append(f'分母r-g={float(R_CNY_MEDIAN - g)*100:.1f}%<5%')
            note = '；'.join(suffix) + ' → 仅情景参考'
            if isinstance(tier_data, dict):
                intrinsic[tier]['advice'] = (orig_advice + ' ' + note).strip()
            else:
                intrinsic[tier] = str(tier_data) + ' ' + note
            notes.append(f'{tier}: {note}')

    if notes:
        result['_intrinsic_note'] = '; '.join(notes)
        logger.warning(f"[终值纪律] {stock.get('code')} {stock.get('name')}: {'; '.join(notes)}")

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

    # 偏好顺序：金融模型优先，其余按效果排。未知新模型自动追加在后。
    PREFERRED_ORDER = [
        "ling-3.0-flash-fin-free",
        "deepseek-v4-flash-free",
        "mimo-v2.5-free",
        "nemotron-3-ultra-free",
    ]

    def __init__(self, api_base: str, api_key: str = None):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self._pool: list[str] = []        # 当前可用模型列表（按偏好排序）
        self._dead: dict[str, float] = {}  # 模型 -> 复活时间戳（TTL，见 mark_dead）
        self._stats: dict[str, dict] = {}  # 模型 -> {ok,fail,timeouts,lat_sum,n_lat}
        self._quality: dict[str, dict] = {}  # 模型 -> {ok,fail} 能力台账（解析+自洽）
        self._dead_ttl = 1800             # 黑名单 TTL（秒），到期自动复活
        self._current = 0                 # 轮换游标
        self._last_refresh = 0.0
        self._refresh_interval = 1800     # 30 秒刷新（首次会立即刷新）
        self._logger = logging.getLogger("FreeModelPool")

    # ── 公开接口 ──

    def acquire(self) -> Optional[str]:
        """性能加权获取：存活模型中得分最高者；同分按游标轮转防饿死。
        全部在黑名单则清空并强制刷新后重试。"""
        now = time.time()
        if now - self._last_refresh > self._refresh_interval or not self._pool:
            self._refresh()
        model = self._pick_alive(now)
        if model is not None:
            return model
        self._logger.warning("[FreeModelPool] 全部模型在黑名单内，清空并强制刷新")
        self._dead.clear()
        self._current = 0
        self._refresh()
        return self._pick_alive(time.time())

    def _alive(self, now=None) -> list[str]:
        """当前存活模型（黑名单未到期者）。"""
        now = now if now is not None else time.time()
        return [m for m in self._pool if self._dead.get(m, 0.0) <= now]

    def _pick_alive(self, now=None) -> Optional[str]:
        """存活者中选最高分；同分按游标起点先见者胜（同分轮转）。"""
        alive = set(self._alive(now))
        if not alive or not self._pool:
            return None
        n = len(self._pool)
        start = self._current % n
        best, best_score = None, None
        for i in range(n):
            m = self._pool[(start + i) % n]
            if m not in alive:
                continue
            s = self.score(m)
            if best is None or s > best_score:
                best, best_score = m, s
        if best is not None:
            self._current = (self._pool.index(best) + 1) % n
        return best

    def mark_dead(self, model: str, ttl: float = None):
        """标记模型不可用至 now+ttl（默认 30 分钟），到期自动复活。
        反复触发会延长禁闭。"""
        self._dead[model] = time.time() + (ttl if ttl is not None else self._dead_ttl)
        alive = len(self._alive())
        self._logger.warning(
            "[FreeModelPool] %s 下线 %.0f 分钟（剩余 %d 个候选）",
            model, (ttl if ttl is not None else self._dead_ttl) / 60, alive)

    def record_result(self, model: str, ok: bool, latency: float = None,
                      timeout: bool = False):
        """记录一次调用结局：成功（含延迟）/失败/超时，用于性能加权。"""
        st = self._stats.setdefault(
            model, {'ok': 0, 'fail': 0, 'timeouts': 0, 'lat_sum': 0.0, 'n_lat': 0})
        if ok:
            st['ok'] += 1
            if latency is not None and latency >= 0:
                st['lat_sum'] += float(latency)
                st['n_lat'] += 1
        else:
            st['fail'] += 1
        if timeout:
            st['timeouts'] += 1

    def score(self, model: str) -> float:
        """总分：0.65 能力分（解析有效率+逻辑自洽率）+ 0.35 可用分 − 超时/延迟惩罚。
        无记录的新模型两项都是中性先验，保证试用机会。能力优先：秒回但废话的
        模型排在慢但高质量的后面；熔断（死亡名单）仍独立生效。"""
        st = self._stats.get(model)
        if st is None:
            avail, timeout_pen, lat_pen = 0.5, 0.0, 0.0
        else:
            n = st['ok'] + st['fail']
            avail = (st['ok'] + 2) / (n + 4)
            timeout_pen = 0.1 * st['timeouts'] / (n + 1)
            lat_pen = 0.0
            if st['n_lat']:
                lat_pen = 0.3 * min(st['lat_sum'] / st['n_lat'], 240.0) / 240.0
        return 0.65 * self.quality_score(model) + 0.35 * avail \
            - timeout_pen - lat_pen

    def record_quality(self, model: str, ok: bool):
        """能力战绩：解析成功且逻辑自洽记 ok，反之记 fail。"""
        q = self._quality.setdefault(model, {'ok': 0, 'fail': 0})
        q['ok' if ok else 'fail'] += 1

    def quality_score(self, model: str) -> float:
        """能力分：Laplace 平滑的能力成功率；无记录返回中性 0.5。"""
        q = self._quality.get(model)
        if q is None:
            return 0.5
        return (q['ok'] + 2) / (q['ok'] + q['fail'] + 4)

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
            # 剪掉已不在池中的黑名单条目（过期条目由 _alive 自然忽略）
            self._dead = {m: t for m, t in self._dead.items() if m in self._pool}
            self._current = 0
            self._logger.info(
                "[FreeModelPool] 发现 %d 个免费模型: %s",
                len(self._pool), ", ".join(self._pool))

        except Exception as e:
            self._logger.warning(
                "[FreeModelPool] 刷新失败: %s，使用已有缓存", e)

    def __repr__(self):
        alive = len(self._alive())
        return f"FreeModelPool({alive}/{len(self._pool)} alive)"


def get_model_pool(api_base: str, api_key: str = None) -> FreeModelPool:
    """获取 FreeModelPool 全局单例。"""
    global _model_pool
    if _model_pool is None:
        _model_pool = FreeModelPool(api_base, api_key)
    return _model_pool


def _retry_after_seconds(resp, default: int) -> int:
    """解析 429 响应的 Retry-After（秒），上限 300。

    缺失/非法时回落到 default（BACKOFF_SCHEDULE 当前档）。
    纯函数，可单测；resp 只需有 .headers.get。
    """
    try:
        raw = resp.headers.get("retry-after")
        if raw is None:
            return default
        return max(0, min(int(float(str(raw).strip())), 300))
    except (ValueError, TypeError, AttributeError):
        return default


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
        # 免 Key 备用通道（主池全灭时兜底，见 _call_fallback_llm）
        fb = cfg.get('fallback') or {}
        self._fb_enabled = bool(fb.get('enabled', True))
        self._fb_base = (fb.get('api_base')
                         or 'https://text.pollinations.ai/openai').rstrip('/')
        self._fb_model = fb.get('model') or 'openai-fast'
        self._fb_temperature = fb.get('temperature', 0.3)
        self._fb_max_tokens = fb.get('max_tokens', 6000)
        self._fb_retries = max(1, int(fb.get('retries', 2)))
        if not self.api_key and not self._is_free_model:
            logger.warning(
                "[AI分析] 未设置 API Key（STOCK_AI_API_KEY / OPENAI_API_KEY），"
                "且模型 %s 不是已知免费模型", self.model
            )
        # 最近一次调用的 token 用量（由 _call_llm 写入，供 analyze_batch 记录日志）
        self._last_usage: Optional[dict] = None
        self._last_error: Optional[str] = None
        # 批内固定模型：一批分析（analyze_batch 一次调用）复用同一模型，失败才轮换
        self._batch_model: Optional[str] = None
        # 同一模型连续 429 计数（_note_429 用，3 次即轮换）
        self._consecutive_429 = 0
        self._last_429_model: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or self._is_free_model

    def analyze_stock(self, stock: dict, extra_instruction: str = None) -> Optional[dict]:
        """对一只股票进行 AI 分析，返回解析后的 dict（失败返回 None）。
        extra_instruction：用户自定义附加要求（拼到 prompt 末尾，队列/自定义分析用）。"""
        if not self.api_key and not self._is_free_model:
            logger.error("[AI分析] 无 API Key，跳过分析")
            self._last_error = '未配置 API Key'
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
        # 数据质量与背景标注（分析师 critique #1：先告诉 AI 数字的来源/日期/置信度）
        quality_facts = _data_quality_facts(stock)
        prompt += "\n\n" + _data_quality_text(quality_facts)
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

        # 用户自定义附加要求（队列/自定义分析经 extra_instruction 传入）
        if extra_instruction and str(extra_instruction).strip():
            prompt += ("\n\n用户附加要求（必须遵守并体现在结论中）：\n"
                       + str(extra_instruction).strip()[:2000])
        content, used_model, usage = self._call_llm(prompt)
        self._last_usage = usage  # 供调用方写入 ai_analysis_log
        if not content and self._fb_enabled:
            logger.info(f"[AI分析] 主通道全灭，{stock.get('code')} 走备用通道 "
                        f"({self._fb_model})...")
            content, used_model, usage = self._call_fallback_llm(prompt)
            self._last_usage = usage
        if not content:
            self._last_error = '模型返回空/主备通道均不可用'
            return None
        logger.info(f"[AI分析] 模型=[{used_model}] {stock.get('code')} {stock.get('name')}")
        result = parse_ai_response(content)
        if result is None:
            if self._pool:
                self._pool.record_quality(used_model, False)
            return None
        if isinstance(result, dict):
            # 质量记账判的是模型原始输出（ enforce 之前）。
            # 分析师整改（2026-09-21）：只有 veto 硬矛盾才记 fail；
            # mirror/六关 tension 只警告（反直觉但自洽的判断应被允许）。
            _cons_issues = _check_output_consistency(result)
            if _cons_issues:
                logger.warning(f"[AI分析] 输出自洽检查未过({used_model}): "
                               f"{_cons_issues}")
            if any(code == 'veto' for code, _ in _cons_issues):
                if self._pool:
                    self._pool.record_quality(used_model, False)
            elif self._pool:
                self._pool.record_quality(used_model, True)
            result = _enforce_verdict_discipline(result)
            result = _enforce_intrinsic_discipline(result, stock)
            # C3 数字抽检：warn-only，只记账永不阻断（失败也不回 None）
            try:
                mm = _check_numeric_citations(
                    result.get('analysis', ''), stock)
            except Exception:
                mm = []
            result['numeric_mismatch'] = mm
            if mm:
                logger.warning(
                    f"[数字抽检] {stock.get('code')} 正文引用与库偏离 "
                    f"{len(mm)} 处: {mm}")
            result['model'] = used_model
            # AI 当时看到的数据质量快照一并落库（可审计"它基于什么做的判断"）
            result['data_quality'] = quality_facts
        return result

    # ── 模型调用（含故障轮换） ──

    def _resolve_model(self) -> str:
        """返回本次应使用的模型名称。免费模型从池中获取，非免费用配置值。

        批内固定：一批分析首次取到的模型会被固定复用（_batch_model），
        直到该模型被标记死亡（_mark_model_dead 会清空 _batch_model），
        之后重新 acquire 换下一个——保证单批 20 只质量一致，批间自动轮换。
        """
        if self._pool:
            if self._batch_model and self._batch_model in self._pool._alive():
                return self._batch_model
            model = self._pool.acquire()
            if model:
                self._batch_model = model
                return model
            logger.warning("[AI分析] 模型池无可用模型，回退到配置值: %s", self.model)
        return self.model

    def _mark_model_dead(self, model: str, timeout: bool = False):
        """标记模型不可用（仅对池中的免费模型生效），同时记一笔失败战绩。"""
        if self._pool:
            self._pool.mark_dead(model)
            self._pool.record_result(model, ok=False, timeout=timeout)
            # 批内固定模型死亡 → 解除固定，下次 _resolve_model 重新 acquire
            if self._batch_model == model:
                self._batch_model = None

    def _note_429(self, model: str) -> bool:
        """记录一次 429。同一模型连续 3 次则返回 True（应轮换），否则 False。
        纯计数逻辑，可单测；换模型自动清零。"""
        if model != self._last_429_model:
            self._last_429_model = model
            self._consecutive_429 = 0
        self._consecutive_429 += 1
        return self._consecutive_429 >= 3

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
            t0 = time.monotonic()
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
                    if self._pool:
                        self._pool.record_result(
                            current_model, ok=True,
                            latency=time.monotonic() - t0)
                    self._consecutive_429 = 0
                    self._last_429_model = None
                    return content, current_model, usage

                # ── 非 200 状态码 ──
                wait_s = BACKOFF_SCHEDULE[attempt]
                should_rotate = self._is_fatal_model_error(resp)
                if should_rotate:
                    self._mark_model_dead(current_model)
                    current_model = self._resolve_model()
                    logger.warning(
                        "[AI分析] HTTP %d (%s), 已轮换至新模型", resp.status_code, current_model)
                elif resp.status_code == 429:
                    # 尊重服务端 Retry-After，避免固定退避持续撞墙延长封禁
                    wait_s = _retry_after_seconds(resp, BACKOFF_SCHEDULE[attempt])
                    self._log_rate_limit(resp, attempt, max_retries)
                    if self._note_429(current_model):
                        logger.warning(
                            "[AI分析] 连续限流 3 次(%s)，轮换模型", current_model)
                        self._mark_model_dead(current_model)
                        current_model = self._resolve_model()
                        self._consecutive_429 = 0
                        self._last_429_model = None
                        time.sleep(2)
                        continue
                else:
                    logger.warning(
                        "[AI分析] HTTP %d, 退避 %ds (%s)",
                        resp.status_code, wait_s, current_model)
                time.sleep(wait_s)

            except httpx.TimeoutException:
                consecutive_timeouts += 1
                logger.warning(
                    "[AI分析] 超时(%s) 第%d次, 退避 %ds",
                    current_model, consecutive_timeouts, BACKOFF_SCHEDULE[attempt])
                if consecutive_timeouts >= 2:
                    logger.warning("[AI分析] 连续超时 %d 次, 轮换模型", consecutive_timeouts)
                    self._mark_model_dead(current_model, timeout=True)
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

    def ask_raw(self, prompt: str, system: str = None) -> tuple:
        """自由问答瘦调用：单次 POST（异常重试 1 次），不进 20 次退避/模型轮换。
        返回 (text, model, usage)，失败 text/usage 为 None。"""
        if not self.api_key:
            self._last_error = '未配置 API Key'
            return None, self.model, None
        system = system or ('你是A股价值投资助手，用中文简洁回答关于这只股票的问题；'
                            '只依据提供的数字，不编造；不确定的直说。')
        headers = {'Content-Type': 'application/json',
                   'Authorization': f'Bearer {self.api_key}'}
        payload = {'model': self.model,
                   'messages': [{'role': 'system', 'content': system},
                                {'role': 'user', 'content': prompt}],
                   'temperature': self.temperature,
                   'max_tokens': self.max_tokens}
        url = f"{self.api_base}/chat/completions"
        for attempt in range(2):
            try:
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    self._last_error = f'HTTP {resp.status_code}'
                    logger.warning("[AI问答] HTTP %d (%s)", resp.status_code, self.model)
                    return None, self.model, None
                data = resp.json()
                text = data['choices'][0]['message']['content']
                if not text or len(text) < 2:
                    self._last_error = '响应内容过短'
                    return None, self.model, None
                _u = data.get('usage') or {}
                usage = {'prompt_tokens': int(_u.get('prompt_tokens', 0) or 0),
                         'completion_tokens': int(_u.get('completion_tokens', 0) or 0),
                         'model': self.model}
                self._last_usage = usage
                return text, self.model, usage
            except (KeyError, IndexError, ValueError) as e:
                self._last_error = f'响应结构异常：{e}'
                logger.warning("[AI问答] 响应结构异常：%s", e)
                return None, self.model, None
            except Exception as e:
                self._last_error = type(e).__name__
                logger.warning("[AI问答] 异常（第%d次）：%s", attempt + 1, e)
                if attempt == 0:
                    time.sleep(3)
        return None, self.model, None

    def _call_fallback_llm(self, prompt: str) -> tuple[Optional[str], Optional[str], Optional[dict]]:
        """免 Key 备用通道：主池全灭时每只股票兜底（轻量：retries 次）。

        返回 (content, used_model, usage)，usage 恒为 None（备用源不记 token）。
        used_model 形如 'fallback/<model>'，落库可溯源。
        """
        if not self._fb_enabled:
            return None, None, None
        url = f"{self._fb_base}/chat/completions"
        for attempt in range(self._fb_retries):
            try:
                with httpx.Client(timeout=240.0) as client:
                    resp = client.post(
                        url,
                        headers={'Content-Type': 'application/json'},
                        json={'model': self._fb_model,
                              'messages': [{'role': 'user', 'content': prompt}],
                              'temperature': self._fb_temperature,
                              'max_tokens': self._fb_max_tokens},
                    )
                if resp.status_code == 200:
                    try:
                        content = resp.json()['choices'][0]['message']['content']
                    except (KeyError, IndexError, ValueError):
                        content = None
                    if content and len(content) >= 10:
                        used = f"fallback/{self._fb_model}"
                        logger.info("[AI分析] 备用通道成功 (%s)", used)
                        return content, used, None
                    logger.warning("[AI分析] 备用通道内容过短，重试 %d/%d",
                                   attempt + 1, self._fb_retries)
                elif resp.status_code == 429:
                    wait_s = _retry_after_seconds(resp, 60)
                    logger.warning("[AI分析] 备用通道限流，退避 %ds (%d/%d)",
                                   wait_s, attempt + 1, self._fb_retries)
                    time.sleep(wait_s)
                    continue
                else:
                    logger.warning("[AI分析] 备用通道 HTTP %d (%d/%d)",
                                   resp.status_code, attempt + 1, self._fb_retries)
                time.sleep(30)
            except (httpx.TimeoutException, httpx.RequestError) as e:
                logger.warning("[AI分析] 备用通道网络错误: %s (%d/%d)",
                               str(e)[:120], attempt + 1, self._fb_retries)
                time.sleep(30)
        logger.error("[AI分析] 备用通道 %d 次全败，放弃", self._fb_retries)
        return None, None, None

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

# ── 数据质量标注（分析师 critique #1：AI 必须知道每个数字的来源、日期与置信度）──
#
# 内部只当参考：不照搬"裸数字 prompt"，每个关键输入附来源与 asof 日期，
# 缺失/兜底字段必须显式告知 AI，不得当作精确值引用。

# prompt 关键字段 → 中文标签（用于缺失清单）
QUALITY_WATCH_FIELDS = [
    ("pe", "PE"), ("pb", "PB"), ("roe", "ROE"),
    ("revenue_growth", "营收增长"), ("profit_growth", "净利增长"),
    ("debt_ratio", "负债率"), ("gross_margin", "毛利率"),
    ("ocf_per_share", "每股OCF"), ("roe_5y_avg", "ROE5年均"),
    ("fcf_5y_sum", "FCF5年累计"), ("roic_5y_avg", "ROIC5年均"),
    ("sector", "行业"),
]


def _parse_iso_date(s):
    """解析 YYYY-MM-DD（容忍 datetime 前缀），失败返回 None。纯函数。"""
    if not s or not isinstance(s, str):
        return None
    try:
        import datetime as _dt
        return _dt.date.fromisoformat(s.strip()[:10])
    except (ValueError, TypeError):
        return None


def _data_quality_facts(stock: dict, today=None) -> dict:
    """从 stock 字典提取数据质量事实（纯函数，缺字段一律记"未知"，永不抛异常）。

    读取（全部可选）：
      run_id/run_date/analysis_date, snapshot_date, financial_updated_at,
      data_years（"2020-2026"）, roe_5y_count, list_date, is_st,
      _market（[{"index_name","change_percent"}]）, 各关键字段（判缺失用）。
    返回 facts dict，供文本渲染与落库/透传共用。
    """
    import datetime as _dt
    facts = {}
    try:
        today_d = _parse_iso_date(today) if today else _dt.date.today()
    except Exception:
        today_d = _dt.date.today()

    run_id = stock.get("run_id")
    run_date = stock.get("run_date")
    if not run_date and run_id and isinstance(run_id, str) and len(run_id) >= 8:
        run_date = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    facts["run_id"] = run_id
    facts["analysis_date"] = (
        stock.get("analysis_date") or run_date
        or (today_d.isoformat() if today_d else "未知")
    )
    facts["snapshot_date"] = stock.get("snapshot_date")
    facts["financial_updated"] = stock.get("financial_updated_at") \
        or stock.get("financial_updated")

    summary = stock.get("_summary") or {}
    facts["data_years"] = stock.get("data_years") or summary.get("data_years")
    try:
        facts["roe_years"] = int(stock.get("roe_5y_count")
                                 if stock.get("roe_5y_count") is not None
                                 else summary.get("roe_5y_count"))
    except (ValueError, TypeError):
        facts["roe_years"] = None

    facts["list_date"] = stock.get("list_date")
    facts["listed_years"] = None
    _ld = _parse_iso_date(facts["list_date"])
    if _ld and today_d:
        try:
            facts["listed_years"] = round((_ld and (today_d - _ld).days) / 365.25, 1)
        except Exception:
            facts["listed_years"] = None

    _st = stock.get("is_st")
    facts["is_st"] = None if _st is None else bool(_st)

    facts["missing"] = [
        label for key, label in QUALITY_WATCH_FIELDS
        if stock.get(key) in (None, "", "N/A")
    ]

    # 双源交叉验算结论（_attach_batch_context 写入；缺数据记 None）
    facts["valuation_check"] = stock.get("_valuation_check")

    facts["market"] = None
    _mkt = stock.get("_market")
    if isinstance(_mkt, list) and _mkt:
        rows = []
        for m in _mkt[:6]:
            if not isinstance(m, dict):
                continue
            chg = m.get("change_percent")
            try:
                chg_s = f"{float(chg):+.2f}%" if chg is not None else "未知"
            except (ValueError, TypeError):
                chg_s = "未知"
            rows.append(f"{m.get('index_name') or m.get('index_code') or '?'} {chg_s}")
        facts["market"] = rows or None
    return facts


def _data_quality_text(facts: dict) -> str:
    """把 facts 渲染为 prompt 用的【数据质量与背景】块。纯函数。"""
    def _v(x):
        return x if x not in (None, "", []) else "未知"

    lines = ["【数据质量与背景（先读这段再下结论）】"]
    lines.append(
        f"- 分析日期：{_v(facts.get('analysis_date'))}"
        f"；筛选批次：{_v(facts.get('run_id'))}"
        f"；行情快照日期：{_v(facts.get('snapshot_date'))}"
        f"；财务汇总更新：{_v(facts.get('financial_updated'))}"
    )
    _ry = facts.get("roe_years")
    lines.append(
        f"- 财务覆盖区间：{_v(facts.get('data_years'))}"
        f"；ROE 5年均基于 {_ry if _ry is not None else '未知'} 年实际数据"
        f"（不足 5 年时均值代表性打折，不得当作长期均值引用）"
    )
    _ly = facts.get("listed_years")
    _st = facts.get("is_st")
    lines.append(
        f"- 上市日期：{_v(facts.get('list_date'))}"
        f"（上市约 {_ly if _ly is not None else '未知'} 年；上市不足 3 年按 C 级处理）"
        f"；ST状态：{'ST（基本面数字可能失真，结论从紧）' if _st else ('正常' if _st is False else '未知')}"
    )
    _mkt = facts.get("market")
    lines.append(
        "- 大盘背景（分析日）：" + ("；".join(_mkt) if _mkt else "无大盘数据")
    )
    _miss = facts.get("missing") or []
    lines.append(
        "- 缺失字段：" + ("无" if not _miss else "、".join(_miss))
        + "——缺失字段不得用于精确结论，只能写“数据不足”。"
    )
    lines.append(
        "- 纪律：凡均值基于不足 5 年数据、或关键字段缺失，估值结论必须降档"
        "（至多给到“灰色地带”），不得写“极具吸引力”类断语。"
    )
    _vc = facts.get("valuation_check")
    if _vc:
        _mc, _pe = _vc.get("market_cap", {}), _vc.get("pe", {})
        _parts = []
        if _mc.get("verdict") in ("WARN", "FAIL"):
            _parts.append(f"市值验算{_mc['verdict']}（现价×股本={_mc.get('calculated_yi')}亿 "
                          f"vs 快照{_mc.get('reported')}亿，偏差{_mc.get('deviation_pct')}%）")
        if _pe.get("verdict") in ("WARN", "FAIL"):
            _parts.append(f"PE验算{_pe['verdict']}（复算{_pe.get('calculated')} "
                          f"vs 快照{_pe.get('reported')}，偏差{_pe.get('deviation_pct')}%）")
        if _parts:
            lines.append(
                "- 双源误差标记：" + "；".join(_parts)
                + "——超1%容差，该字段置信度降级，不得作为精确值引用，估值结论从紧。"
            )
        elif _mc.get("verdict") == "SKIP" and _pe.get("verdict") == "SKIP":
            # P0-2：双源都缺数据时如实报“未执行”，不许静默 SKIP 谎报通过；
            # 跳过原因（findings）透传进 prompt。
            _notes = list(dict.fromkeys(
                n for n in (_mc.get("note"), _pe.get("note")) if n))
            lines.append(
                "- 双源验算未执行：市值/PE 均因缺数据跳过（"
                + "；".join(_notes)
                + "）——这不是通过；估值关键数字未交叉验证，关键结论必须按缺数据降档。"
            )
        else:
            _skip = [f"{'市值' if k == 'market_cap' else 'PE'}"
                     f"跳过：{_vc[k].get('note')}"
                     for k in ("market_cap", "pe")
                     if (_vc.get(k) or {}).get("verdict") == "SKIP"]
            lines.append(
                "- 双源误差标记：市值/PE 双源验算通过（偏差≤1%容差）"
                + ("；" + "；".join(_skip) if _skip else "") + "。"
            )
    return "\n".join(lines)


def _attach_batch_context(stocks: list[dict], run_id: str) -> None:
    """批内上下文：run_id 回填 + 快照字段（is_st/list_date/snapshot_date）富集
    + 取一次大盘快照挂到每只 stock._market（原地修改）
    + 双源误差标记（市值：现价×总股本 vs 快照；PE：年报复算 vs 快照，1%容差）。

    失败永不抛异常（取不到的字段如实记"未知"，见 _data_quality_facts）。
    """
    try:
        from src.models.database import MarketIndexDAO, StockSnapshotDAO
        market = MarketIndexDAO().get_latest()
        snap_dao = StockSnapshotDAO()
    except Exception as e:
        logger.warning(f"[AI分析] 批上下文初始化失败（不阻断）: {e}")
        market, snap_dao = None, None
    for s in stocks:
        try:
            s.setdefault("run_id", run_id)
            if snap_dao is not None and isinstance(s, dict):
                try:
                    snap = snap_dao.get_by_code(s.get("code", ""))
                except Exception:
                    snap = None
                if snap:
                    s.setdefault("is_st", snap.get("is_st"))
                    s.setdefault("list_date", snap.get("list_date"))
                    s.setdefault("snapshot_date", snap.get("snapshot_date"))
                    if s.get("sector") in (None, "", "未知"):
                        s["sector"] = snap.get("sector") or s.get("sector", "未知")
                    # 双源误差标记（复用 B1 验算逻辑，1%/5% 容差；永不抛异常）
                    try:
                        from scripts.verify_valuation import (
                            verify_market_cap, verify_ratios)
                        s["_valuation_check"] = {
                            "market_cap": verify_market_cap(
                                snap.get("current_price"),
                                s.get("total_shares"),
                                snap.get("market_cap")),
                            "pe": verify_ratios(
                                snap.get("current_price"),
                                s.get("eps"), None,
                                snap.get("pe"), None)["pe"],
                        }
                    except Exception:
                        s["_valuation_check"] = None
            if "_market" not in s:
                s["_market"] = market
        except Exception:
            pass


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
        analysis_json, trade_json, result.get('model'))

    # 论文落库（论点+假设+红线+卖出条件，复盘时更新假设状态）
    thesis = result.get('thesis')
    if isinstance(thesis, dict) and thesis.get('core_thesis'):
        try:
            from src.models.database import WatchlistThesisDAO
            WatchlistThesisDAO.upsert(
                stock['code'], str(thesis['core_thesis'])[:500],
                thesis.get('assumptions') or [],
                thesis.get('red_lines') or [],
                thesis.get('sell_conditions') or [],
                source='ai_analysis',
            )
        except Exception as e:
            logger.warning(f"[AI分析] 论文落库失败（不阻断）: {e}")


def _save_failure(stock: dict, run_id: str, reason: str = None):
    """记录一次失败尝试（写入空记录 + 标记失败原因，便于前端/复盘可见）。"""
    from src.models.database import ScreeningResultDAO, StockAnalysisHistoryDAO
    ScreeningResultDAO().mark_ai_failure(run_id, stock['code'], reason or 'unknown')
    StockAnalysisHistoryDAO().save(
        stock['code'], run_id, stock.get('score'), '{}', '{}', None)


def build_qa_context(stock: dict) -> str:
    """拼单股问答上下文（快照 + 财务摘要 + 筛选行，紧凑文本）。"""
    def _g(k, d='未知'):
        v = stock.get(k)
        return d if v in (None, '') else v
    lines = [f"【{_g('name')} {_g('code')}】现价{_g('current_price')} PE{_g('pe')} "
             f"PB{_g('pb')} ROE{_g('roe')}% 市值{_g('market_cap')}亿 "
             f"行业{_g('sector')} 评分{_g('score')}"]
    if stock.get('reason'):
        lines.append(f"入选理由：{stock['reason']}")
    for k, label in (('roe_5y_avg', 'ROE5年均'), ('gross_margin', '毛利率'),
                     ('fcf_5y_sum', '5年累计FCF'), ('debt_ratio', '资产负债率'),
                     ('revenue_growth', '营收增长'), ('profit_growth', '净利增长')):
        v = stock.get(k)
        if v is not None and v != '':
            lines.append(f"{label}：{v}")
    return "\n".join(lines)


def answer_question(stock: dict, question: str) -> Optional[dict]:
    """单股自由问答：返回 {answer, model, usage}，失败返回 None。"""
    from src.config import load_config
    q = (question or '').strip()
    if not q:
        return None
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    if not analyzer.configured:
        analyzer._last_error = '未配置 API Key'
        return None
    prompt = build_qa_context(stock) + "\n\n用户问题：" + q[:500]
    text, model, usage = analyzer.ask_raw(prompt)
    if text is None:
        return None
    return {'answer': text, 'model': model, 'usage': usage or {}}


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
            _save_failure(s, run_id, '未配置 API Key')
        return 0, total

    logger.info(f"[AI分析] 开始分析 {total} 只股票...")
    analyzed_ok = 0
    analyzed_failed = 0

    # 批内上下文：run_id/快照字段/大盘快照（供数据质量标注，失败不阻断）
    _attach_batch_context(stocks, run_id)

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
            _save_failure(stock, run_id, getattr(analyzer, '_last_error', None) or '分析失败')
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
