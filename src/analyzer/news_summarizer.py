"""新闻舆情 AI 摘要（2026-10-19）。

定位：AI 不只看数字，也要看舆论与情绪。逐条判定情绪（利好/中性/利空）
并给一句话解读，再合成一篇当日舆情日报（Markdown）落 news_digest，
渲染在投资笔记页。

设计要点：
- 逐条判情绪写回 news_item，滚动条才能显示情绪标签；日报是聚合视图。
- 条目太多时按相关度取前 N 条（标题命中股票名/代码权重高），省 token。
- 失败不抛：返回 None，调用方按「无摘要」处理。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SENTIMENTS = ("利好", "中性", "利空")
NL = chr(10)

PROMPT_HEAD = (
    "你是市场舆情分析师。下面是某只 A 股相关的新闻条目，请逐条判断对股价的影响倾向。" + NL +
    NL +
    "判断标准（严格三选一）：" + NL +
    "- 利好：业绩超预期、订单/政策利好、机构上调评级、重大资产注入等正向催化" + NL +
    "- 利空：业绩暴雷、减持/解禁、监管处罚、退市风险、行业政策利空等负向冲击" + NL +
    "- 中性：常规公告、行业综述、观点性文章、无明确方向" + NL +
    NL +
    "不确定就判中性，不要硬凑方向。" + NL +
    NL +
    "【输出 JSON】（不要输出其他任何内容）" + NL +
    "{" + NL +
    "  judgments: [{id, sentiment, brief}], overall: 一句话结论, content_md: Markdown正文" + NL +
    "}"
)


def _rank(items: list, code_name: dict) -> list:
    """相关度排序：命中股票名/代码的排前面，其次新的排前面。"""
    def _score(it):
        s = 0
        name = code_name.get(it.get("code"), "")
        blob = (it.get("title") or "") + " " + (it.get("summary") or "")
        if name and name in blob:
            s += 10
        if it.get("code") and it["code"] in blob:
            s += 5
        return s

    return sorted(items,
                  key=lambda it: (_score(it), it.get("published_at") or ""),
                  reverse=True)


def _build_prompt(items: list, code_name: dict, digest_date: str) -> str:
    parts = [PROMPT_HEAD, "", "【新闻条目】"]
    for it in items:
        label = code_name.get(it.get("code"), "") or it.get("code") or "市场"
        head = "- id=" + str(it["id"]) + "｜" + label + "｜"
        head += (it.get("published_at") or "时间未知") + "｜" + str(it.get("title") or "")
        parts.append(head)
        if it.get("summary"):
            parts.append("  摘要：" + str(it["summary"])[:200])
    parts.append("")
    parts.append("日期用 " + digest_date + "。content_md 用 Markdown。")
    return NL.join(parts)


def summarize_news(analyzer, items: list, code_name: dict, digest_date: str,
                   scope: str = "", max_items: int = 30) -> Optional[dict]:
    """对新闻做逐条情绪判定 + 生成舆情日报。失败返回 None。"""
    if not analyzer or not getattr(analyzer, "configured", False) or not items:
        return None
    picked = _rank(items, code_name)[:max_items]
    prompt = _build_prompt(picked, code_name, digest_date)
    try:
        raw = analyzer.ask_raw(prompt, system="你只输出JSON，不要输出其他任何内容。")
    except Exception as e:
        logger.warning("[新闻摘要] LLM 调用失败: %s", e)
        return None
    if isinstance(raw, tuple):
        text = raw[0]
        model = raw[1] if len(raw) > 1 else ""
        usage = raw[2] if len(raw) > 2 else {}
    else:
        text, model, usage = raw, "", {}
    data = None
    try:
        t = (text or "").strip()
        if "```" in t:
            parts = t.split("```")
            t = parts[1] if len(parts) > 1 else t
            if t.startswith("json"):
                t = t[4:]
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e > s:
            data = json.loads(t[s:e + 1])
    except Exception as ex:
        logger.warning("[新闻摘要] JSON 解析失败: %s", ex)
    if not isinstance(data, dict):
        return None

    judgments = []
    valid_ids = {it["id"] for it in picked}
    for j in (data.get("judgments") or []):
        if not isinstance(j, dict) or j.get("id") not in valid_ids:
            continue
        sent = str(j.get("sentiment") or "").strip()
        judgments.append({
            "id": j["id"],
            "sentiment": sent if sent in SENTIMENTS else "中性",
            "brief": str(j.get("brief") or "")[:120],
        })
    content_md = str(data.get("content_md") or "").strip()
    if not content_md:
        return None
    return {
        "overall": str(data.get("overall") or "")[:200],
        "content_md": content_md,
        "judgments": judgments,
        "usage": usage or {},
        "model": model,
        "scope": scope,
    }
