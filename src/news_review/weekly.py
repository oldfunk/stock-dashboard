"""周度收盘检阅汇总（F2，2026-11-28）。

把本周（周一至周五）的日笔记聚合成一篇周报：每日要点、重复主题、P0/P1/P2
事件、组合实际收益、下周关注、来源。确定性生成（不再调 LLM），不与周六
观察池复盘抢同一条 `ai_journal`，单独存 `news_weekly`。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Optional

from src.utils import now_cn

_SECTION_RE = re.compile(r"(?m)^##\s*(\d+)\.[^\n]*$")


def week_bounds(week_end: Optional[str] = None) -> tuple[str, str]:
    """返回 (周一, 周五)；传入周末则回退到最近周五。"""
    d = (date.fromisoformat(week_end) if week_end
         else date.today())
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return (d - timedelta(days=4)).isoformat(), d.isoformat()


def section(md: str, n: int) -> str:
    """取 Markdown 第 n 节正文（到下一个 ## 为止）。"""
    matches = list(_SECTION_RE.finditer(md or ""))
    for i, m in enumerate(matches):
        if int(m.group(1)) != n:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        return md[start:end].strip()
    return ""


def _json(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "null") or default
    except (TypeError, ValueError):
        return default


def _first_line(text: str, limit: int = 120) -> str:
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-* ").strip()
        if line:
            return line[:limit]
    return ""


def diff_reviews(reviews: list[dict]) -> dict:
    """跨日 diff：来源代码覆盖天数、重复/新增/消失。"""
    per_day = []
    for r in reviews:
        sources = _json(r.get("sources_json"), [])
        codes = {str(s.get("code") or "").zfill(6)
                 for s in sources if s.get("code")}
        per_day.append((r.get("journal_date"), codes))
    counts: dict[str, int] = {}
    for _, codes in per_day:
        for c in codes:
            counts[c] = counts.get(c, 0) + 1
    repeated = sorted(c for c, n in counts.items() if n >= 2)
    first = per_day[0][1] if per_day else set()
    last = per_day[-1][1] if per_day else set()
    return {"per_day": {d: sorted(c) for d, c in per_day},
            "repeated": repeated,
            "new_on_last": sorted(last - first),
            "dropped_on_last": sorted(first - last)}


def build_weekly(reviews: list[dict], events: list[dict],
                 week_start: str, week_end: str) -> dict:
    title = f"{week_start} → {week_end} 收盘检阅周报"
    if not reviews:
        md = (f"# {title}\n\n本周没有收盘检阅（可能停牌/假期/调度未运行）。\n")
        return {"title": title, "content_md": md, "sources": [],
                "actions": {"reviews": 0}}

    sources_map = {}
    for r in reviews:
        for s in _json(r.get("sources_json"), []):
            if s.get("id") is None:
                continue
            from src.news_review import rules
            if rules.is_noise({"title": s.get("title") or ""}):
                continue
            sources_map[int(s["id"])] = s
    actions_list = [_json(r.get("actions_summary"), {}) for r in reviews]
    diff = diff_reviews(reviews)

    lines = [f"# {title}", "", "## 1. 每日要点", "",
             "| 日期 | 新闻 | 持仓命中 | 组合 asof | 市场首句 |",
             "|---|---:|---:|---|---|"]
    for r, a in zip(reviews, actions_list):
        market = _first_line(section(r.get("content_md") or "", 1))
        lines.append(
            f"| {r.get('journal_date')} | {a.get('item_count', '--')} | "
            f"{a.get('holdings_hit', '--')} | {a.get('portfolio_asof') or '--'} | "
            f"{market} |")

    lines += ["", "## 2. 重复主题", ""]
    if diff["repeated"]:
        lines.append("本周多日被引用的标的：" + "、".join(diff["repeated"]))
    else:
        lines.append("无跨日重复标的。")

    lines += ["", "## 3. 重大事件", ""]
    if events:
        lines += ["| 日期 | 级别 | 事件 | 来源 |", "|---|---|---|---|"]
        for e in events:
            lines.append(f"| {e.get('trade_date')} | {e.get('level')} | "
                         f"{str(e.get('title') or '')[:80]} | "
                         f"{e.get('source') or ''} |")
    else:
        lines.append("本周无 P0/P1/P2 事件。")

    lines += ["", "## 4. 组合实际收益", ""]
    first_actual = (actions_list[0] or {}).get("portfolio_actual") or {}
    last_actual = (actions_list[-1] or {}).get("portfolio_actual") or {}
    if last_actual:
        lines.append(
            f"- 周初累计 {first_actual.get('return_str') or '--'} → "
            f"周末累计 {last_actual.get('return_str') or '--'}；"
            f"已实现 {last_actual.get('realized_pnl')}，费用 {last_actual.get('fees_total')}，"
            f"最大回撤 {last_actual.get('mdd_str') or '--'}。")
        lines.append(f"- 数据截至 {last_actual.get('asof') or '--'}（子项目面板/只读桥）。")
    else:
        lines.append("本周笔记未记录实际收益字段（旧版笔记），只看 NAV 与持仓。")

    lines += ["", "## 5. 下周关注", "",
              section(reviews[-1].get("content_md") or "", 6) or "无。",
              "", "## 6. 来源", ""]
    if sources_map:
        for s in sorted(sources_map.values(), key=lambda x: int(x["id"]),
                        reverse=True)[:40]:
            url = f" {s['url']}" if s.get("url") else ""
            lines.append(f"- [news#{s['id']}] {s.get('title') or ''} — "
                         f"{s.get('source') or ''} {s.get('published_at') or ''}{url}")
    else:
        lines.append("- 无可用来源。")
    lines += ["", "本汇总由日笔记确定性聚合，不构成投资建议。"]

    return {
        "title": title,
        "content_md": "\n".join(lines),
        "sources": list(sources_map.values()),
        "actions": {
            "reviews": len(reviews),
            "dates": [r.get("journal_date") for r in reviews],
            "items": sum(int(a.get("item_count") or 0) for a in actions_list),
            "holdings_hit": sum(int(a.get("holdings_hit") or 0)
                                for a in actions_list),
            "events": len(events),
            "diff": diff,
            "portfolio_first": first_actual,
            "portfolio_last": last_actual,
        },
    }


def run_weekly(week_end: Optional[str] = None, dry_run: bool = False) -> dict:
    from src.models.news_review import (NewsEventDAO, NewsReviewDAO,
                                        NewsWeeklyDAO)

    start, end = week_bounds(week_end)
    dao = NewsReviewDAO()
    d = datetime.strptime(start, "%Y-%m-%d").date()
    reviews = []
    while d.isoformat() <= end:
        row = dao.get_review(d.isoformat())
        if row:
            reviews.append(row)
        d += timedelta(days=1)
    events = [e for e in NewsEventDAO().recent(limit=200)
              if start <= str(e.get("trade_date") or "") <= end]
    events.sort(key=lambda x: (x.get("trade_date") or "", x.get("event_time") or ""))
    built = build_weekly(reviews, events, start, end)
    if not dry_run:
        NewsWeeklyDAO().save(end, start, built["title"], built["content_md"],
                             built["sources"], built["actions"])
    return {"ok": True, "week_start": start, "week_end": end,
            "dry_run": dry_run, **built}
