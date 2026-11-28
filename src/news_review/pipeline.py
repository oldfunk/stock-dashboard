"""收盘检阅流水线（F1，2026-11-28）。

新闻入库（已有）→ 去重/实体链接/级别 → 相关性排序 → 市场/组合事实
→ LLM 结构化判断 + Markdown → 直接发布到 `ai_journal`（AI 自主）。
不写纸盘、不触发交易；失败降级为确定性摘要，仍带来源。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from src.news_review import rules
from src.news_review.facts import (facts_for_prompt, market_facts,
                                   portfolio_facts, target_facts,
                                   target_universe)
from src.news_review.prompt import (JSON_SYS, TEXT_SYS, build_facts_prompt,
                                    build_markdown_prompt)
from src.utils import now_cn

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Optional[dict]:
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
    except Exception as ex:                       # noqa: BLE001
        logger.warning("[收盘检阅] JSON 解析失败: %s", ex)
        return None


def _day_window(date: str) -> tuple[str, str]:
    d = datetime.strptime(date, "%Y-%m-%d")
    return f"{date} 00:00:00", (d + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")


def _fmt_pct(v) -> str:
    return "--" if v is None else f"{v * 100:+.2f}%"


def _roles_for(news: list[dict], targets: list[dict]) -> dict[str, str]:
    role_map: dict[str, str] = {}
    for t in targets:
        for r in t.get("roles") or []:
            if t["code"] not in role_map or (
                    rules.role_priority(r) < rules.role_priority(role_map[t["code"]])):
                role_map[t["code"]] = r
    return role_map


def link_news(news: list[dict], targets: list[dict]) -> list[dict]:
    """实体链接 + 聚类 + 级别；返回带 linkage 字段的副本（不写库）。"""
    code_name = {t["code"]: t.get("name") or "" for t in targets}
    target_codes = set(code_name)
    out = []
    for it in news:
        item = dict(it)
        entities = rules.entities_for(
            f"{item.get('title') or ''} {item.get('summary') or ''}", code_name)
        item["entities"] = entities
        item["cluster_id"] = rules.cluster_key(item.get("title") or "")
        level, etype = rules.severity_for(item, target_codes)
        item["severity"], item["event_type"] = level or "", etype
        item["official"] = 1 if rules.is_official(item) else 0
        out.append(item)
    return out


def _fallback_markdown(date: str, facts: dict, news: list[dict],
                       reason: str) -> str:
    m = facts.get("market") or {}
    lines = [f"# {date} 收盘检阅（AI 自主）", "", "## 1. 市场事实"]
    idx = [i for i in (m.get("indices") or []) if i.get("close") is not None]
    if idx:
        lines.append("、".join(
            f"{i['name']} {i['close']:.2f}（{_fmt_pct(i.get('pct'))}）"
            for i in idx))
    else:
        lines.append("行情数据不可用。")
    b = m.get("breadth") or {}
    if b.get("total"):
        lines.append(f"全市场上涨 {b['up']} / 下跌 {b['down']} / 平 "
                     f"{b['flat']}（样本 {b['total']}）。行情库口径。")
    t = m.get("turnover") or {}
    if t:
        lines.append(f"两市成交额约 {t['cur_yi']:.0f} 亿元"
                     f"（前一日 {t['prev_yi']:.0f} 亿元，"
                     f"{_fmt_pct(t.get('pct'))}）。")

    lines += ["", "## 2. 持仓与观察池"]
    focus = [x for x in facts.get("targets") or []
             if "持仓" in (x.get("roles") or [])] or facts.get("targets") or []
    if not focus:
        lines.append("无持仓/观察池数据。")
    for x in focus[:10]:
        rel = [n for n in news if x["code"] in (n.get("entities") or [])
               or n.get("code") == x["code"]]
        tail = ""
        if rel:
            tail = "；相关新闻：" + "、".join(
                f"[news#{n['id']}] {n['title'][:40]}" for n in rel[:3])
        lines.append(
            f"- {x['code']} {x.get('name') or ''}："
            f"收盘 {x.get('close') if x.get('close') is not None else '--'}"
            f"（{_fmt_pct(x.get('pct'))}）{tail}")

    lines += ["", "## 3. 候选池"]
    cand = [x for x in facts.get("targets") or []
            if "候选" in (x.get("roles") or [])
            and not set(x.get("roles") or []) & {"持仓", "观察池", "钉选"}]
    cand_hit = [x for x in cand if any(
        x["code"] in (n.get("entities") or []) or n.get("code") == x["code"]
        for n in news)]
    if cand_hit:
        for x in cand_hit[:5]:
            lines.append(f"- {x['code']} {x.get('name') or ''}：有相关新闻，"
                         f"收盘 {_fmt_pct(x.get('pct'))}")
    else:
        lines.append("今日候选池无影响级新闻。")

    lines += ["", "## 4. 组合变量"]
    p = facts.get("portfolio") or {}
    if p.get("available"):
        nav = p.get("nav") or {}
        lines.append(f"- 纸盘 NAV {nav.get('total_value') or '--'}，"
                     f"累计 {_fmt_pct(nav.get('pnl_pct'))}，"
                     f"截至 {nav.get('timestamp') or '--'}；"
                     f"现金 {p.get('cash') if p.get('cash') is not None else '--'}。")
        pos = p.get("positions") or []
        if pos:
            lines.append("- 持仓：" + "、".join(
                f"{x['symbol']} {x['total_volume']} 股" for x in pos))
        plan = p.get("pending_plan")
        if plan:
            lines.append(f"- 待执行计划 #{plan.get('id')}（{plan.get('plan_date')}）。")
    else:
        lines.append("纸盘桥不可用；组合变量缺失。")

    lines += ["", "## 5. 纪律与风险",
              "本笔记只做事实与风险复核；新闻不构成交易指令，交易仍由母策略决定。",
              "", "## 6. 下周关注",
              "1. 持仓相关公告与政策落地情况；",
              "2. 观察池论点的可验证指标；",
              "3. 纸盘计划执行与费用。",
              "", "## 7. 来源"]
    if news:
        for n in news[:20]:
            url = f" {n['url']}" if n.get("url") else ""
            lines.append(f"- [news#{n['id']}] {n.get('title') or ''} — "
                         f"{n.get('source') or '来源未知'} "
                         f"{n.get('published_at') or ''}{url}")
    else:
        lines.append("- 今日无可用要闻（来源不可用）。")
    lines += ["", f"（降级原因：{reason}）",
              "", "本笔记由 AI 自主生成，不构成投资建议。"]
    return "\n".join(lines)


def collect_inputs(date: str, limit: int = 200,
                   max_items: int = 30) -> tuple[dict, list[dict], list[dict]]:
    """采集事实、新闻、目标池；新闻已做实体/聚类/级别（不写库）。"""
    from src.models.database import db_conn
    from src.models.news import NewsDAO

    with db_conn() as conn:
        targets = target_facts(conn, date)
        facts = {"market": market_facts(conn, date),
                 "targets": targets,
                 "portfolio": portfolio_facts()}

    start, end = _day_window(date)
    dao = NewsDAO()
    raw = dao.between(start, end, limit=limit)
    if not raw:
        # 当日无入库（例如源晚到）：回看窗口内最新条目，但保留真实发布时间
        since = (datetime.strptime(date, "%Y-%m-%d") - timedelta(hours=24)
                 ).strftime("%Y-%m-%d %H:%M:%S")
        raw = dao.between(since, end, limit=limit)
    news = rules.dedupe(link_news(raw, targets))
    roles = _roles_for(news, targets)
    news = rules.rank(news, roles)[:int(max_items)]
    return facts, news, targets


def run_close_review(analyzer=None, date: Optional[str] = None,
                     dry_run: bool = False, cfg: Optional[dict] = None) -> dict:
    """生成并发布收盘检阅；失败不抛，返回 {ok, status, reason}。"""
    from src.models.news import NewsDAO
    from src.models.news_review import NewsEventDAO, NewsReviewDAO

    started = time.monotonic()
    date = date or now_cn().strftime("%Y-%m-%d")
    cfg = cfg or {}
    news_cfg = (cfg.get("news") or {}) if isinstance(cfg, dict) else {}
    max_items = int(news_cfg.get("review_max_items", 30))

    facts, news, targets = collect_inputs(date, max_items=max_items)
    if news:
        NewsDAO().apply_linkage(news)
    target_codes = {t["code"] for t in targets}
    holding_codes = {t["code"] for t in targets if "持仓" in (t.get("roles") or [])}
    events = rules.build_events(
        news, {t["code"]: t.get("name") or "" for t in targets},
        now=f"{date} 15:45:00",
        limits=news_cfg.get("event_limits") or {"P0": 5, "P1": 10})

    if analyzer is None:
        from src.analyzer.ai_analyzer import AiAnalyzer
        analyzer = AiAnalyzer((cfg.get("ai") or {}) if isinstance(cfg, dict) else {})
    if not getattr(analyzer, "configured", False):
        return {"ok": False, "status": "error", "reason": "analyzer_unconfigured",
                "date": date, "item_count": len(news)}

    model, usage = "", {}
    judgments: dict = {}
    content_md = ""
    degraded = ""
    if news:
        try:
            text, model, usage = analyzer.ask_raw(
                build_facts_prompt(date, facts, news), system=JSON_SYS)
            data = _extract_json(text) or {}
            judgments = data if isinstance(data, dict) else {}
        except Exception as e:                    # noqa: BLE001
            logger.warning("[收盘检阅] 判断调用失败: %s", e)
            degraded = f"judge_failed:{type(e).__name__}"
        try:
            text2, model2, usage2 = analyzer.ask_raw(
                build_markdown_prompt(date, facts, judgments, news),
                system=TEXT_SYS)
            content_md = (text2 or "").strip()
            if model2:
                model = model2
            if usage2:
                usage = {**usage, **usage2}
        except Exception as e:                    # noqa: BLE001
            logger.warning("[收盘检阅] 正文调用失败: %s", e)
            degraded = f"text_failed:{type(e).__name__}"
    else:
        degraded = "no_news"

    if not content_md:
        content_md = _fallback_markdown(date, facts, news,
                                        degraded or "llm_empty")
        if not degraded:
            degraded = "llm_empty"

    valid_ids = [n["id"] for n in news]
    cited = rules.referenced_ids(content_md)
    missing = rules.missing_citations(content_md, valid_ids)
    advice = rules.advice_hits(content_md)
    redline_hits = sum(1 for x in (judgments.get("items") or [])
                       if isinstance(x, dict) and x.get("redline"))

    sources = [{"id": n["id"], "title": n.get("title"), "url": n.get("url"),
                "source": n.get("source"),
                "published_at": n.get("published_at"),
                "code": n.get("code")} for n in news]
    actions = {
        "model": model, "usage": usage, "item_count": len(news),
        "holdings_hit": sum(1 for n in news
                            if set(n.get("entities") or []) & holding_codes),
        "redline_hits": int(redline_hits),
        "event_count": len(events),
        "citation_count": len(cited), "citation_missing": missing,
        "advice_hits": advice, "degraded": degraded,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "generated_at": now_cn().isoformat(),
        "disclaimer": "AI 自主生成，不构成投资建议",
    }
    title = f"{date} 收盘检阅（AI 自主）"

    if not dry_run:
        saved = NewsReviewDAO().save_review(
            date, title, content_md, sources, facts, actions,
            run_id=f"news_review:{date}",
            status="published" if not degraded else "published",
            model=model)
        NewsEventDAO().upsert_many(events)
        NewsReviewDAO().save_run(
            date, "degraded" if degraded else "ok",
            item_count=len(news), holdings_hit=actions["holdings_hit"],
            redline_hits=actions["redline_hits"], event_count=len(events),
            duration_ms=actions["duration_ms"], model=model, error=degraded)
        actions["saved"] = saved

    return {"ok": True, "status": "degraded" if degraded else "ok",
            "date": date, "item_count": len(news), "event_count": len(events),
            "holding_items": actions["holdings_hit"],
            "content_md": content_md, "sources": sources,
            "actions": actions, "dry_run": dry_run}
