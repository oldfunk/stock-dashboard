"""新闻抓取（2026-10-19）。

设计：源可插拔（registry），当前只有东财个股新闻（AKShare stock_news_em）作为临时源。
换真实媒体时只需在 registry 里注册新 Source，DAO/UI/摘要都不动——
按需求「先把功能做完善，媒体后找」，源的可靠性不是本轮重点。

统一返回字段：{code, name, title, summary, url, source, published_at}
任何源失败都吞掉并返回空列表：新闻是增强项，不能拖垮采集链路。
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


def _normalize_time(raw) -> str:
    """源站时间 → YYYY-MM-DD HH:MM（认不准就返回空，交由 fetched_at 兜底排序）。"""
    s = str(raw or '').strip()
    if not s:
        return ''
    digits = ''.join(ch for ch in s if ch.isdigit())
    if len(digits) >= 14:
        return digits[0:4] + '-' + digits[4:6] + '-' + digits[6:8] + ' ' + digits[8:10] + ':' + digits[10:12]
    if len(digits) == 8:
        return digits[0:4] + '-' + digits[4:6] + '-' + digits[6:8]
    return ''


MARKET_CODE = '__market__'          # 市场级新闻在 fetch_log 里的伪代码
_URL_DATE_RE = re.compile('/(20[0-9]{2}-[0-9]{2}-[0-9]{2})/')


def _date_from_url(url) -> str:
    """从 URL 里抠发布日期（/YYYY-MM-DD/），抠不到返回空。"""
    m = _URL_DATE_RE.search(str(url or ''))
    return m.group(1) if m else ''


def _headline(text, max_len: int = 60) -> str:
    """正文首句当标题（财新源没有独立标题字段）。

    先剥掉「【本文系数据通用户提前专享】」这类栏目/付费提示前缀，
    否则滚动条里全是同一句提示语。
    """
    s = str(text or '').strip()
    while s.startswith('【'):
        end = s.find('】')
        if end < 0:
            break
        s = s[end + 1:].strip()
    if not s:
        return ''
    for sep in ('。', '！', '？', chr(10)):
        i = s.find(sep)
        if 0 < i <= max_len:
            return s[:i]
    return s[:max_len] + ('…' if len(s) > max_len else '')


class AkshareEmSource:
    """东方财富个股新闻（AKShare stock_news_em）——临时默认源。"""

    name = 'akshare_em'

    def fetch(self, code: str, name: str = '', limit: int = 10) -> list:
        try:
            import akshare as ak

            df = ak.stock_news_em(symbol=str(code))
        except Exception as e:
            logger.warning("[新闻] %s 东财抓取失败: %s", code, e)
            return []
        if df is None or len(df) == 0:
            return []
        out = []
        for _, row in df.head(limit).iterrows():
            title = str(row.get('新闻标题') or '').strip()
            if not title:
                continue
            content = str(row.get('新闻内容') or '').strip()
            out.append({
                'code': str(code),
                'name': name or str(row.get('股票名称') or ''),
                'title': title,
                'summary': content[:500],
                'url': str(row.get('新闻链接') or '').strip(),
                'source': self.name,
                'published_at': _normalize_time(row.get('发布时间')),
            })
        return out



class CaixinHotSource:
    """财新头条（AKShare stock_news_main_cx）——市场级源。

    只返回 tag(栏目)/summary(正文摘要)/url：标题取正文首句，
    发布时间从 URL 的 /YYYY-MM-DD/ 解析；url 去掉查询参数以免去重失效。
    """

    name = 'caixin_hot'
    market_level = True

    def fetch(self, code: str, name: str = '', limit: int = 10) -> list:
        return []          # 市场级源不按股票抓

    def fetch_market(self, limit: int = 20) -> list:
        try:
            import akshare as ak

            df = ak.stock_news_main_cx()
        except Exception as e:
            logger.warning("[新闻] 财新抓取失败: %s", e)
            return []
        if df is None or len(df) == 0:
            return []
        out = []
        for _, row in df.head(limit).iterrows():
            summary = str(row.get('summary') or '').strip()
            if not summary:
                continue
            url = str(row.get('url') or '').strip().split('?')[0]
            out.append({
                'code': '',
                'name': '市场',
                'title': _headline(summary),
                'summary': summary[:500],
                'url': url,
                'source': self.name,
                'published_at': _date_from_url(url),
            })
        return out


_REGISTRY = {}


def register_source(cls):
    _REGISTRY[cls.name] = cls
    return cls


register_source(AkshareEmSource)
register_source(CaixinHotSource)


def get_source(name: str = 'akshare_em') -> Optional[object]:
    cls = _REGISTRY.get(name)
    return cls() if cls else None


def get_sources(names) -> list:
    """按名字取源实例，未知源跳过。"""
    return [src for src in (get_source(n) for n in (names or [])) if src]


def sources_from_config(cfg: dict) -> list:
    """配置里的源列表：优先 sources，兼容旧的单源 source。"""
    cfg = cfg or {}
    raw = cfg.get('sources')
    if raw is None:
        raw = cfg.get('source') or 'akshare_em'
    if isinstance(raw, str):
        raw = [raw]
    return [str(x) for x in raw if x]


def target_codes(include_pinned: bool = True, include_pool: bool = False,
                 include_watchlist_ai: bool = True, limit: int = 20) -> list:
    """要抓新闻的 (code, name) 清单。默认：钉选 + AI 观察池。"""
    out = []
    seen = set()

    def _add(code, name=''):
        c = str(code or '').strip()
        if not c or c in seen:
            return
        seen.add(c)
        out.append((c, str(name or '')))

    if include_pinned:
        try:
            from src.models.database import WatchlistDAO
            for row in WatchlistDAO().list_all():
                _add(row.get('code'), row.get('name'))
        except Exception as e:
            logger.warning("[新闻] 钉选读取失败: %s", e)
    if include_watchlist_ai:
        try:
            from src.models.ai_watchlist import AiWatchlistDAO
            for row in AiWatchlistDAO().get_all():
                _add(row.get('code'), row.get('name'))
        except Exception as e:
            logger.warning("[新闻] 观察池读取失败: %s", e)
    if include_pool:
        try:
            from src.models.database import ScreeningResultDAO
            for row in ScreeningResultDAO().get_latest_results(limit=limit):
                _add(row.get('code'), row.get('name'))
        except Exception as e:
            logger.warning("[新闻] 候选池读取失败: %s", e)
    return out


def fetch_news(codes=None, source: str = 'akshare_em', per_code: int = 10) -> list:
    """批量抓取并去重（同一 URL/标题只留一条）。单只失败不影响其他。"""
    src = get_source(source)
    if src is None:
        logger.warning("[新闻] 未知源 %s，跳过", source)
        return []
    codes = codes if codes is not None else target_codes()
    merged = []
    for code, name in codes:
        try:
            merged.extend(src.fetch(code, name, limit=per_code))
        except Exception as e:
            logger.warning("[新闻] %s 抓取异常: %s", code, e)
    seen, out = set(), []
    for it in merged:
        key = (it.get('url') or '', it.get('title') or '')
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def ingest(sources=None, per_code: int = 10, cooldown_min: int = 0,
           market_per_run: int = 0, **scope) -> dict:
    """抓取 → 落库，返回统计摘要（供 API/定时任务用）。

    sources 可传单个源名或列表：个股源按 target_codes 抓，市场级源
    （market_level=True，如财新）抓大盘要闻，落库时 code=''。
    cooldown_min > 0 时跳过最近抓过的对象：个股按票、市场级按 MARKET_CODE。
    """
    from src.models.news import NewsDAO
    if sources is None:
        sources = ['akshare_em']
    if isinstance(sources, str):
        sources = [sources]
    srcs = get_sources(sources)
    dao = NewsDAO()

    stock_srcs = [x for x in srcs if not getattr(x, 'market_level', False)]
    market_srcs = [x for x in srcs if getattr(x, 'market_level', False)]

    codes = target_codes(**scope)
    skipped = 0
    if cooldown_min > 0 and codes:
        hot = dao.recently_fetched([c for c, _ in codes], cooldown_min)
        if hot:
            skipped = len(hot)
            codes = [(c, n) for c, n in codes if c not in hot]
    items = []
    for src in stock_srcs:
        items.extend(fetch_news(codes, source=src.name, per_code=per_code))
    added = dao.upsert_many(items)
    if stock_srcs:
        # 记录尝试（含空结果）：冷却按尝试算，抓不到内容的票也不会被反复打
        dao.mark_fetched([c for c, _ in codes], ok=bool(items))

    market_added, market_skipped = 0, False
    if market_srcs and market_per_run > 0:
        if cooldown_min > 0 and dao.recently_fetched([MARKET_CODE], cooldown_min):
            market_skipped = True
        else:
            mkt = []
            for src in market_srcs:
                mkt.extend(src.fetch_market(limit=market_per_run))
            market_added = dao.upsert_many(mkt)
            dao.mark_fetched([MARKET_CODE], ok=bool(mkt))

    logger.info("[新闻] 个股源 %d 个：%d 只（冷却跳过 %d）/ %d 条，新增 %d；"
                "市场级新增 %d%s", len(stock_srcs), len(codes), skipped,
                len(items), added, market_added,
                "（冷却跳过）" if market_skipped else "")
    return {'codes': len(codes), 'skipped': skipped, 'fetched': len(items),
            'added': added, 'market_added': market_added,
            'market_skipped': market_skipped}
