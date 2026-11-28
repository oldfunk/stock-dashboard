"""收盘检阅提示词（F 线，2026-11-28）。

两次调用：第一次结构化判断（小 JSON，便于校验），第二次 Markdown 正文。
提示词把纪律写死：价值视角、事实/解释分层、必须带 `[news#id]`、禁止买卖建议。
"""
from __future__ import annotations

import json

NL = chr(10)
JSON_SYS = "你只输出JSON，不要输出其他任何内容。"
TEXT_SYS = "你只输出Markdown正文，不要输出其他任何内容。"


def _news_block(items: list[dict], limit: int = 30) -> str:
    lines = []
    for it in items[:limit]:
        lines.append(
            f"- news#{it['id']}｜{it.get('published_at') or '时间未知'}"
            f"｜{it.get('source') or '来源未知'}"
            f"｜{it.get('code') or '市场'}"
            f"｜{it.get('title') or ''}")
        if it.get("summary"):
            lines.append(f"  摘要：{str(it['summary'])[:220]}")
    return NL.join(lines) or "- （无）"


def build_facts_prompt(date: str, facts: dict, news: list[dict]) -> str:
    return NL.join([
        f"你是A股价值投资研究助手。日期：{date}。",
        "下面是市场事实、组合变量（含实际收益）、标的清单与今日新闻。请逐条判断新闻对标的的影响，",
        "并把组合的实际收益/回撤/费用作为判断背景，不要只看净值。",
        "",
        "纪律（必须遵守）：",
        "1. 只从价值/基本面/政策/风险角度分析；禁止短线、资金流、情绪、涨跌预测。",
        "2. 不得给出买入/卖出/加仓/减仓/清仓/止损/目标价等任何交易建议。",
        "3. 事实与解释分开：新闻事实只来自给定条目，不得补充外部事实或旧闻。",
        "4. 没有影响就写“无”，不要凑相关性。",
        "5. relevance 只能是：持仓/观察池/候选/市场/无；code 用 6 位代码或空串。",
        "6. 市场/组合数据来自系统（行情库、纸盘只读桥），不是 news_item，不要给它们编造 news id。",
        "",
        "【市场事实与组合变量】",
        json.dumps(facts, ensure_ascii=False, default=str),
        "",
        "注：百分比字段同时给了原始小数（pct/pnl_pct）和展示串（pct_str/nav_pct_str）；正文一律使用 *_str，不要自己换算。",
        "",
        "【新闻】",
        _news_block(news),
        "",
        "输出 JSON（只输出这个对象）：",
        '{"market": "一句话：市场事实 + 对价值组合的含义",',
        ' "items": [{"news_id": 1, "code": "000792", "relevance": "持仓",',
        '            "impact": "事实→影响（不超过80字）",',
        '            "thesis_link": "触及/未触及/无论点", "redline": false}],',
        ' "focus": ["下周可验证的关注点，最多3条"]}',
    ])


def build_markdown_prompt(date: str, facts: dict, judgments: dict,
                          news: list[dict], max_chars: int = 1200) -> str:
    return NL.join([
        f"写一篇 {date} 的收盘检阅，读者是价值投资组合的持有人。",
        "标题固定为：收盘检阅（AI 自主）。",
        "",
        "【结构（必须全部出现）】",
        "## 1. 市场事实",
        "## 2. 持仓与观察池",
        "## 3. 候选池",
        "## 4. 组合变量",
        "## 5. 纪律与风险",
        "## 6. 下周关注",
        "## 7. 来源",
        "",
        "【写作要求】",
        f"1. 全文不超过 {max_chars} 字；只写有信息量的内容。只有新闻列表为空时，才写“今日无影响级要闻”。",
        "2. 新闻事实句必须带引用，格式 `[news#12]`；来源缺失写“来源不可用”，不得编造。",
        "3. 市场事实与组合变量来自系统数据库/纸盘桥，不要给它们加 `[news#]` 引用；写“行情库”或“纸盘桥”即可。",
        "4. 解释与事实分开写；不得使用“利好/利空/资金流入/主力/情绪”等短线词。",
        "5. 严禁任何交易建议（买入/卖出/加仓/减仓/目标价/止损），新闻不构成交易指令；复盘成交时用“成交价 vs 计划价”，不写“建议买入价”。",
        "6. 第 2 节优先写持仓；第 3 节只列有相关新闻的候选，不覆盖全部 20 只。",
        "7. 第 4 节写组合变量与**实际收益**：优先用 portfolio.actual 的 total_return / total_pnl / realized_pnl / fees_total / win_rate / mdd / exposure / today_pnl；区分已实现与未实现、费用；带 asof；不要只写 NAV。source=paper:panel 时注明“子项目 :8081 面板”，否则注明“只读桥”。",
        "8. 第 5 节只做纪律复盘：本周计划/成交/费用/回撤；不预测下周涨跌。",
        "9. 第 7 节逐条列出来源：`[news#id] 标题 — 来源 时间 URL`；没有 URL 就省略 URL。",
        "10. 结尾写一句：“本笔记由 AI 自主生成，不构成投资建议。”",
        "",
        "【市场事实与组合变量】",
        json.dumps(facts, ensure_ascii=False, default=str),
        "",
        "注：百分比字段同时给了原始小数（pct/pnl_pct）和展示串（pct_str/nav_pct_str）；正文一律使用 *_str，不要自己换算。",
        "",
        "【逐条判断】",
        json.dumps(judgments, ensure_ascii=False, default=str),
        "",
        "【新闻（引用时必须用这些 id）】",
        _news_block(news),
    ])
