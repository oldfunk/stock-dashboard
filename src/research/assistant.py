"""R4-lite：研究实验复核助手（2026-11-28）。

只读研究表，调用研究助手模型，输出一份 Markdown 备忘录；
不写策略、不下单、不修改实验/假设/决策。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from src.research.dao import ResearchDAO
from src.utils import now_cn

CITATION_RE = re.compile(r"\[([HER]-[A-Za-z0-9-]+)\]")
ADVICE_RE = re.compile(
    r"(建议|应当|可以|考虑|值得|不妨)[^。；\n]{0,12}"
    r"(买入|卖出|加仓|减仓|清仓|建仓|止盈|止损)")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
NL = chr(10)


def experiment_facts(experiment_id: str, dao: Optional[ResearchDAO] = None) -> dict:
    dao = dao or ResearchDAO()
    exp = dao.get_experiment(experiment_id)
    if not exp:
        raise ValueError(f"实验不存在: {experiment_id}")
    hyp = dao.get_hypothesis(exp.get("hypothesis_id") or "") or {}
    results = dao.list_results(experiment_id)
    return {
        "hypothesis": {k: hyp.get(k) for k in
                       ("id", "statement", "prior", "metric", "threshold",
                        "horizon_days", "benchmark", "kill_criteria", "status")},
        "experiment": {k: exp.get(k) for k in
                       ("id", "hypothesis_id", "status", "verdict", "notes",
                        "code_commit", "config_hash", "data_snapshot_id",
                        "plan")},
        "results": [{k: r.get(k) for k in
                     ("id", "metric", "value", "ci_low", "ci_high",
                      "p_value", "sample_size", "verdict", "splits")}
                    for r in results],
    }


def build_prompt(facts: dict) -> str:
    return NL.join([
        "你是量化研究复核助手。只根据下面的事实复核实验，不要补充外部知识。",
        "",
        "【事实】",
        json.dumps(facts, ensure_ascii=False, indent=2, default=str),
        "",
        "【任务】输出 Markdown，四节：",
        "1. 结论是否被结果支持（区分统计显著 / 样本不足 / 多重比较）",
        "2. 设计与执行问题（样本、分域、前视、费用、数据质量）",
        "3. 证据缺口（还缺什么才能下结论）",
        "4. 下一步（只写研究动作，不写交易动作）",
        "",
        "【硬规则】",
        "- 每个事实句末尾必须引用来源，格式 [H-002] / [E-H002b] / [R-20261112-005]；",
        "- 不得引用未给出的 ID；不得编造数字；数字必须来自事实；",
        "- 严禁买入/卖出/加仓/减仓/目标价等交易建议；",
        "- 不确定就写“证据不足”，不要猜。",
    ])


def _allowed_numbers(facts: dict) -> set[str]:
    out = set()
    for r in facts.get("results") or []:
        for k in ("value", "ci_low", "ci_high", "p_value", "sample_size"):
            v = r.get(k)
            if v is not None:
                out.add(str(v))
    return out


def validate_memo(md: str, facts: dict) -> dict:
    valid_ids = {facts.get("hypothesis", {}).get("id"),
                 facts.get("experiment", {}).get("id")}
    valid_ids |= {r.get("id") for r in facts.get("results") or []}
    valid_ids.discard(None)
    cited = set(CITATION_RE.findall(md or ""))
    unknown = sorted(cited - valid_ids)
    advice = [m.group(0) for m in ADVICE_RE.finditer(md or "")]
    allowed = _allowed_numbers(facts)
    numeric_warnings = sorted({n for n in NUMBER_RE.findall(md or "")
                               if n not in allowed and len(n) > 1
                               and not re.fullmatch(r"20\d{2}", n)})
    return {
        "citations": sorted(cited),
        "unknown_citations": unknown,
        "advice_hits": advice,
        "number_warnings": numeric_warnings[:10],
        "ok": not unknown and not advice,
    }


def review_experiment(experiment_id: str, analyzer=None,
                      cfg: Optional[dict] = None) -> dict:
    """生成实验复核备忘录；不写库。失败返回 ok=False。"""
    if analyzer is None:
        from src.llm_config import research_analyzer
        analyzer = research_analyzer()
    if analyzer is None or not getattr(analyzer, "configured", False):
        return {"ok": False, "reason": "research_assistant_disabled_or_unconfigured"}
    facts = experiment_facts(experiment_id)
    prompt = build_prompt(facts)
    text, model, usage = analyzer.ask_raw(prompt, system="你只输出Markdown正文。",
                                          timeout=300.0)
    md = (text or "").strip()
    if not md:
        return {"ok": False, "reason": "llm_empty", "experiment_id": experiment_id}
    checks = validate_memo(md, facts)
    return {
        "ok": checks["ok"],
        "experiment_id": experiment_id,
        "content_md": md,
        "model": model,
        "usage": usage or {},
        "checks": checks,
        "generated_at": now_cn().isoformat(),
    }
