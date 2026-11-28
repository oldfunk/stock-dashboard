"""新闻实体/级别/引用规则（F 线纯函数，2026-11-28）。

原则（`docs/close-review-design.md` §F1.5）：
- 规则先行、LLM 只做解释；严重度不交给模型判。
- P0 政策/监管：官方主体 + 政策关键词；P1 公司重大：仅限持仓/观察池/候选。
- 新闻不触发交易；事件只进下一次计划窗口的复核清单。
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Optional

OFFICIAL_BODIES = (
    "国务院", "证监会", "人民银行", "央行", "财政部", "发改委", "工信部",
    "卫健委", "医保局", "住建部", "金融监管总局", "商务部", "交易所",
    "国家税务总局", "海关总署", "市场监管总局",
)

POLICY_KEYWORDS = (
    "印花税", "过户费", "交易制度", "T+0", "涨跌停", "减持新规", "退市",
    "注册制", "IPO", "再融资", "降准", "降息", "LPR", "存款准备金",
    "集采", "价格管制", "关税", "出口管制", "行业监管", "反垄断",
)

COMPANY_EVENT_KEYWORDS = {
    "立案调查": ("立案", "立案调查"),
    "行政处罚": ("行政处罚", "处罚决定", "罚款"),
    "财务造假": ("财务造假", "虚假记载", "舞弊"),
    "审计非标": ("非标意见", "保留意见", "无法表示意见", "否定意见"),
    "业绩转亏": ("预亏", "首亏", "续亏", "业绩预告下修", "由盈转亏"),
    "重大重组": ("重大资产重组", "发行股份购买资产", "控制权变更"),
    "停牌": ("停牌", "暂停上市"),
    "退市风险": ("退市风险警示", "*ST", "终止上市"),
    "重大诉讼": ("重大诉讼", "仲裁"),
    "质押风险": ("质押爆仓", "平仓风险", "质押违约"),
    "大额减持": ("大额减持", "减持计划", "清仓式减持"),
}

ADVICE_PATTERN = re.compile(
    r"(建议|应当|可以|考虑|值得|不妨)[^。；\n]{0,12}"
    r"(买入|卖出|加仓|减仓|清仓|建仓|止盈|止损|抄底|追高)"
    r"|(目标价|止损价|买入价|卖出价)\s*[:：]?\s*[0-9]"
)
CITATION_RE = re.compile(r"\[news#(\d+)\]")
_WS_PUNCT = re.compile(r"[\s，。！？、；：\"'“”‘’（）()【】\[\]<>《》\-—_/\\|.,!?;:]+")


def normalize_title(title: str) -> str:
    return _WS_PUNCT.sub("", str(title or "").lower())


def cluster_key(title: str) -> str:
    """标题归一化后的短哈希：同一事件的多家转载归到一组。"""
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:12]


def _blob(item: dict) -> str:
    return f"{item.get('title') or ''} {item.get('summary') or ''}"


def is_official(item: dict) -> bool:
    if int(item.get("official") or 0):
        return True
    src = str(item.get("source") or "")
    if src in ("gov", "csrc"):
        return True
    text = _blob(item)
    return any(b in text for b in OFFICIAL_BODIES)


def _match_keyword(text: str, keywords: Iterable[str]) -> Optional[str]:
    for kw in keywords:
        if kw in text:
            return kw
    return None


def event_type_for(text: str, level: str) -> str:
    if level == "P0":
        return _match_keyword(text, POLICY_KEYWORDS) or "policy"
    for etype, kws in COMPANY_EVENT_KEYWORDS.items():
        if _match_keyword(text, kws):
            return etype
    return "company"


def severity_for(item: dict, target_codes: set[str]) -> tuple[Optional[str], str]:
    """返回 (P0/P1/None, event_type)。

    P0 认定比 P1 严：官方主体 + 政策关键词，或命中强政策词（印花税/降准等）。
    仅命中"关税"等宽词时仍要求官方主体，避免行业新闻全被抬成 P0。
    """
    text = _blob(item)
    official = is_official(item)
    strong = ("印花税", "过户费", "交易制度", "T+0", "涨跌停", "减持新规",
              "降准", "降息", "存款准备金", "注册制", "退市风险警示")
    if _match_keyword(text, strong) or (official and _match_keyword(text, POLICY_KEYWORDS)):
        return "P0", event_type_for(text, "P0")
    code = str(item.get("code") or "").strip()
    linked = {str(c) for c in (item.get("entities") or [])}
    if code:
        linked.add(code)
    if linked & target_codes:
        if _match_keyword(text, [k for kws in COMPANY_EVENT_KEYWORDS.values()
                                 for k in kws]):
            return "P1", event_type_for(text, "P1")
    return None, ""


def entities_for(text: str, targets: dict[str, str]) -> list[str]:
    """按代码/股票名匹配实体；股票名至少 2 字，避免单字误伤。"""
    blob = str(text or "")
    out = []
    for code, name in targets.items():
        c = str(code).zfill(6)
        n = str(name or "").strip()
        if c in blob or (len(n) >= 2 and n in blob):
            out.append(c)
    return out


def dedupe(items: list[dict]) -> list[dict]:
    """同 cluster 只留一条；优先带 URL、有发布时间、来源不是转发的。"""
    best: dict[str, dict] = {}
    for it in items:
        key = cluster_key(it.get("title") or "")
        cur = best.get(key)
        if cur is None:
            best[key] = it
            continue
        score = lambda x: (bool(x.get("url")), bool(x.get("published_at")))
        if score(it) > score(cur):
            best[key] = it
    return list(best.values())


def role_priority(role: str) -> int:
    return {"持仓": 0, "观察池": 1, "钉选": 2, "候选": 3, "市场": 4}.get(role, 5)


def rank(items: list[dict], roles: dict[str, str]) -> list[dict]:
    """相关性排序：持仓 > 观察池 > 钉选 > 候选 > 市场；同层按时间倒序。"""
    def _role(it):
        codes = list(it.get('entities') or [])
        if it.get('code'):
            codes.insert(0, str(it['code']))
        if not codes:
            return role_priority('市场')
        return min(role_priority(roles.get(str(c), '市场')) for c in codes)

    recent_first = sorted(items, key=lambda it: str(it.get('published_at') or ''),
                          reverse=True)
    return sorted(recent_first, key=_role)


def event_bucket(ts: str) -> str:
    """30 分钟时间桶（用于同实体+同类型冷却）。"""
    s = str(ts or "")
    if len(s) >= 16:
        return s[:13] + "_" + ("0" if int(s[14:16]) < 30 else "1")
    return s[:13] or "unknown"


def build_events(items: list[dict], targets: dict[str, str], now: str,
                 limits: Optional[dict] = None) -> list[dict]:
    """规则引擎输出事件（不调 LLM、不写库）；同键去重由 DB 唯一索引兜底。"""
    limits = limits or {"P0": 5, "P1": 10}
    target_codes = set(targets)
    events, seen = [], set()
    counts = {"P0": 0, "P1": 0}
    for it in items:
        level, etype = severity_for(it, target_codes)
        if not level:
            continue
        if counts[level] >= int(limits.get(level, 10)):
            continue
        ts = str(it.get("published_at") or it.get("fetched_at") or now)
        code = str(it.get("code") or "")
        key = f"{level}:{code or 'market'}:{etype}:{event_bucket(ts)}"
        if key in seen:
            continue
        seen.add(key)
        counts[level] += 1
        events.append({
            "event_key": key,
            "event_time": ts,
            "trade_date": str(now)[:10],
            "level": level,
            "event_type": etype,
            "code": code,
            "name": it.get("name") or targets.get(code) or "",
            "title": str(it.get("title") or "")[:200],
            "fact": str(it.get("title") or "")[:500],
            "inference": "",
            "source": it.get("source"),
            "url": it.get("url"),
            "news_id": it.get("id"),
            "official": is_official(it),
        })
    return events


def referenced_ids(md: str) -> set[int]:
    return {int(x) for x in CITATION_RE.findall(str(md or ""))}


def missing_citations(md: str, valid_ids: Iterable[int]) -> list[int]:
    valid = {int(x) for x in valid_ids}
    return sorted(referenced_ids(md) - valid)


def advice_hits(md: str) -> list[str]:
    return [m.group(0).strip() for m in ADVICE_PATTERN.finditer(str(md or ""))]
