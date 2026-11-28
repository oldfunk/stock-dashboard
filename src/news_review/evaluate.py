"""收盘检阅评估（F5，2026-11-28）。

自动指标：章节完整性、引用有效性、买卖建议数、来源数、实际收益 asof。
人工标注（可选）：给定 `{news_id: {relevant, role}}` 时算引用 precision/recall。
只读，不改笔记；`--check` 有硬失败时 exit 1，供收盘后巡检。
"""
from __future__ import annotations

import json
from typing import Optional

from src.news_review import rules

REQUIRED_SECTIONS = (
    "## 1. 市场事实",
    "## 2. 持仓与观察池",
    "## 3. 候选池",
    "## 4. 组合变量",
    "## 5. 纪律与风险",
    "## 6. 下周关注",
    "## 7. 来源",
)


def _json_field(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def evaluate_review(row: dict,
                    annotations: Optional[dict] = None) -> dict:
    md = str(row.get("content_md") or "")
    actions = _json_field(row.get("actions_summary"), {})
    sources = _json_field(row.get("sources_json"), [])
    valid = {int(s["id"]) for s in sources if s.get("id") is not None}
    cited = rules.referenced_ids(md)
    invalid = sorted(cited - valid)
    advice = rules.advice_hits(md)
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in md]
    failures = []
    if not md.strip():
        failures.append("empty_content")
    if missing_sections:
        failures.append("missing_sections")
    if invalid:
        failures.append("invalid_citations")
    if advice:
        failures.append("advice_present")

    metrics = {
        "journal_date": row.get("journal_date"),
        "status": row.get("status"),
        "chars": len(md),
        "sections": len(REQUIRED_SECTIONS) - len(missing_sections),
        "missing_sections": missing_sections,
        "sources": len(sources),
        "sources_with_url": sum(1 for s in sources if s.get("url")),
        "citations": len(cited),
        "invalid_citations": invalid,
        "advice_hits": advice,
        "portfolio_asof": actions.get("portfolio_asof"),
        "portfolio_source": actions.get("portfolio_source"),
        "item_count": actions.get("item_count"),
        "holdings_hit": actions.get("holdings_hit"),
        "event_count": actions.get("event_count"),
        "degraded": actions.get("degraded"),
    }
    if annotations:
        items = annotations.get("items") if isinstance(annotations, dict) else None
        items = items if isinstance(items, dict) else (annotations or {})
        relevant = {int(k) for k, v in items.items()
                    if isinstance(v, dict) and v.get("relevant")}
        hit = cited & relevant
        metrics["annotation_relevant"] = len(relevant)
        metrics["annotation_hit"] = len(hit)
        metrics["precision"] = (len(hit) / len(cited)) if cited else None
        metrics["recall"] = (len(hit) / len(relevant)) if relevant else None
    metrics["failures"] = failures
    metrics["ok"] = not failures
    return metrics
