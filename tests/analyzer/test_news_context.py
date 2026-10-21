"""AI 分析新闻上下文单测（2026-10-21）。

hermetic：fake DAO，不联网、不建库。
"""

from datetime import datetime

from src.analyzer.ai_analyzer import build_news_context


class FakeNewsDAO:
    def __init__(self, items=None, digest=None):
        self.items = items or []
        self.digest = digest
        self.called = None

    def by_code(self, code, limit=20, since=None):
        self.called = {"code": code, "limit": limit, "since": since}
        return self.items

    def latest_digest(self):
        return self.digest


def _item(**kw):
    base = {
        "published_at": "2026-10-20 09:30",
        "source": "东方财富",
        "sentiment": "利空",
        "title": "公司股东拟减持2%股份",
        "ai_brief": "短期抛压，需核实减持计划",
    }
    base.update(kw)
    return base


NOW = datetime(2026, 10, 21, 12, 0)


class TestBuildNewsContext:
    def test_includes_items_keywords_and_digest(self):
        dao = FakeNewsDAO(
            items=[_item()],
            digest={"digest_date": "2026-10-20", "overall": "震荡偏弱",
                    "content_md": "市场缩量，红利防守。"},
        )
        ctx = build_news_context("600519", news_dao=dao, now=NOW)
        assert "公司股东拟减持2%股份" in ctx
        assert "利空" in ctx
        assert "减持×1" in ctx
        assert "市场缩量" in ctx
        assert "震荡偏弱" in ctx
        assert "禁止编造" in ctx
        assert dao.called["code"] == "600519"
        assert dao.called["since"] == "2026-10-07"

    def test_no_news_is_explicit_not_fabricated(self):
        ctx = build_news_context("600519", news_dao=FakeNewsDAO(), now=NOW)
        assert "未抓取到该股新闻" in ctx
        assert "不代表没有事件" in ctx
        # 没有新闻时不应出现伪造的日期条目
        assert "2026-10-20" not in ctx

    def test_old_digest_is_excluded(self):
        dao = FakeNewsDAO(digest={
            "digest_date": "2026-10-01", "overall": "旧结论",
            "content_md": "旧日报",
        })
        ctx = build_news_context("600519", news_dao=dao, now=NOW)
        assert "旧结论" not in ctx
        assert "市场背景" not in ctx

    def test_dao_exception_degrades_to_missing(self):
        class Broken:
            def by_code(self, *a, **k):
                raise RuntimeError("db down")

            def latest_digest(self):
                raise RuntimeError("db down")

        ctx = build_news_context("600519", news_dao=Broken(), now=NOW)
        assert "未抓取到该股新闻" in ctx

    def test_empty_code_returns_empty(self):
        assert build_news_context("", news_dao=FakeNewsDAO(), now=NOW) == ""
