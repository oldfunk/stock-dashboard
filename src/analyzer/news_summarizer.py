"""新闻舆情 AI 摘要（2026-10-19）。

定位：AI 不只看数字，也要看舆论与情绪。逐条判定情绪（利好/中性/利空）
并给一句话解读，再合成一篇当日舆情日报（Markdown）落 news_digest，
渲染在投资笔记页。

关键设计：两次调用，不把 Markdown 塞进 JSON。
第一次只回结构化的逐条判定（小 JSON，好解析）；第二次回纯文本 Markdown。
实测让模型在 JSON 字符串里写正文，它会在正文里打引号导致整段解析失败，
所以正文单独一次更稳——这与本项目大盘解盘写 Markdown 的既有做法一致。

失败不抛：返回 None，调用方按「无摘要」处理。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SENTIMENTS = ("利好", "中性", "利空")
NL = chr(10)
JSON_SYS = "你只输出JSON，不要输出其他任何内容。"
TEXT_SYS = "你只输出Markdown正文，不要输出其他任何内容。"

JUDGE_PROMPT = (
    "你是市场舆情分析师。下面是若干条 A 股新闻，请逐条判断对股价的影响倾向。" + NL +
    NL +
    "判断标准（严格三选一）：" + NL +
    "- 利好：业绩超预期、大额订单/中标、政策扶持、机构上调评级等正向催化" + NL +
    "- 利空：业绩暴雷、减持解禁、监管处罚、退市风险、行业政策收紧等负向冲击" + NL +
    "- 中性：常规公告、行业综述、观点文章、无明确方向" + NL +
    NL +
    "不确定就判中性，不要硬凑方向。brief 用 30 字内说清为什么影响股价。" + NL +
    NL +
    "输出 JSON，格式严格如下（只输出这个对象）：" + NL +
    '{"judgments": [{"id": 1, "sentiment": "利好", "brief": "订单落地"}],'
    ' "overall": "当日舆情一句话结论"}'
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


def _news_block(items: list, code_name: dict) -> str:
    lines = []
    for it in items:
        label = code_name.get(it.get("code"), "") or it.get("code") or "市场"
        lines.append("- id=" + str(it["id"]) + "｜" + label + "｜" +
                     (it.get("published_at") or "时间未知") + "｜" +
                     str(it.get("title") or ""))
        if it.get("summary"):
            lines.append("  摘要：" + str(it["summary"])[:180])
    return NL.join(lines)


def _extract_json(text: str) -> Optional[dict]:
    """从模型回复里抠出 JSON 对象；失败返回 None。"""
    try:
        t = (text or "").strip()
        if "```" in t:
            parts = t.split("```")
            t = parts[1] if len(parts) > 1 else t
            if t.lower().startswith("json"):
                t = t[4:]
        s, e = t.find("{"), t.rfind("}")
        if s == -1 or e <= s:
            return None
        data = json.loads(t[s:e + 1])
        return data if isinstance(data, dict) else None
    except Exception as ex:
        logger.warning("[新闻摘要] JSON 解析失败: %s", ex)
        return None


def _ask(analyzer, prompt: str, system: str):
    """调 LLM，返回 (text, model, usage)；异常抛给上层处理。"""
    raw = analyzer.ask_raw(prompt, system=system)
    if isinstance(raw, tuple):
        return (raw[0], raw[1] if len(raw) > 1 else "",
                raw[2] if len(raw) > 2 else {})
    return (raw, "", {})


def summarize_news(analyzer, items: list, code_name: dict, digest_date: str,
                   scope: str = "", max_items: int = 30) -> Optional[dict]:
    """两次调用：① 逐条情绪判定(JSON) ② 舆情日报正文(Markdown)。

    返回 {overall, content_md, judgments, usage, model, scope}，失败 None。
    """
    if not analyzer or not getattr(analyzer, "configured", False) or not items:
        return None
    picked = _rank(items, code_name)[:max_items]
    block = _news_block(picked, code_name)

    # ① 逐条判定
    judgments: list = []
    overall = ""
    model, usage = "", {}
    try:
        text, model, usage = _ask(analyzer, JUDGE_PROMPT + NL + NL + block,
                                  JSON_SYS)
        data = _extract_json(text)
        if data:
            overall = str(data.get("overall") or "")[:200]
            valid = {it["id"] for it in picked}
            for j in (data.get("judgments") or []):
                if not isinstance(j, dict) or j.get("id") not in valid:
                    continue
                sent = str(j.get("sentiment") or "").strip()
                judgments.append({
                    "id": j["id"],
                    "sentiment": sent if sent in SENTIMENTS else "中性",
                    "brief": str(j.get("brief") or "")[:120],
                })
    except Exception as e:
        logger.warning("[新闻摘要] 判定调用失败: %s", e)

    # ② 正文（纯 Markdown，模型自由写）
    # 注：判定列表为空时用「- （无）」兜底，该 or 必须只包住列表本身——
    # 若留在整串拼接外层，`+` 优先于 `or` 会让左侧（开头提示词，必非空）短路掉
    # 后面的【新闻条目】与写作要求，日报就变成凭一行 overall 自由发挥（审计 N2）。
    body_prompt = (
        "下面是某组 A 股标的的舆情判定结果与新闻条目。请写一篇当日舆情日报。" + NL +
        NL +
        "【判定汇总】" + NL +
        (overall or "（无）") + NL +
        (NL.join(["- [" + j["sentiment"] + "] " + j["brief"] for j in judgments])
         or "- （无）") + NL + NL +
        "【新闻条目】" + NL + block + NL + NL +
        "要求（Markdown）：" + NL +
        "1. 开头一句话给当日市场情绪结论" + NL +
        "2. 分标的写：舆情倾向 + 对该股意味着什么（结合基本面/持仓视角）" + NL +
        "3. 最后一段：对持仓与观察池的启示，以及需要警惕的信号" + NL +
        "4. 不要复述原文标题，用人话；不要写免责套话" + NL +
        "5. 日期写 " + digest_date
    )
    try:
        text, model2, usage2 = _ask(analyzer, body_prompt, TEXT_SYS)
        content_md = (text or "").strip()
        if usage2:
            usage = {**usage, **usage2}
        if model2:
            model = model2
    except Exception as e:
        logger.warning("[新闻摘要] 正文调用失败: %s", e)
        content_md = ""

    if not content_md:
        return None
    if not overall and judgments:
        pos = sum(1 for j in judgments if j["sentiment"] == "利好")
        neg = sum(1 for j in judgments if j["sentiment"] == "利空")
        overall = ("整体偏正面" if pos > neg else
                   "整体偏负面" if neg > pos else "多空交织，偏中性")
    return {
        "overall": overall,
        "content_md": content_md,
        "judgments": judgments,
        "usage": usage or {},
        "model": model,
        "scope": scope,
    }
