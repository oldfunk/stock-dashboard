"""新闻舆情功能单测（2026-10-19）：DAO 幂等/排序/回写、源解析、摘要 JSON 容错、路由。

hermetic：一律 tmp 库 + monkeypatch 掉真实抓取，绝不联网。
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "t.db"))
    db_mod.init_database()
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from src.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(tmp_path / "n.db"))
    db_mod.init_database()
    return db_mod


def _item(code="300390", title="宁德时代获大额订单", url="http://x/1",
          pub="2026-10-19 10:00", name="天华新能"):
    return {"code": code, "name": name, "title": title, "summary": "正文",
            "url": url, "source": "test", "published_at": pub}


# ── DAO ──────────────────────────────────────────────────────

def test_upsert_is_idempotent(tmp_db):
    from src.models.news import NewsDAO
    dao = NewsDAO()
    assert dao.upsert_many([_item()]) == 1
    assert dao.upsert_many([_item()]) == 0, "同 URL+标题不该重复入库"
    assert len(dao.latest()) == 1


def test_upsert_skips_blank_title(tmp_db):
    from src.models.news import NewsDAO
    assert NewsDAO().upsert_many([_item(title="  ")]) == 0


def test_latest_orders_by_published_desc(tmp_db):
    from src.models.news import NewsDAO
    dao = NewsDAO()
    dao.upsert_many([
        _item(title="旧", url="u1", pub="2026-10-01 09:00"),
        _item(title="新", url="u2", pub="2026-10-19 09:00"),
    ])
    assert [r["title"] for r in dao.latest()] == ["新", "旧"]


def test_for_codes_and_unsummarized(tmp_db):
    from src.models.news import NewsDAO
    dao = NewsDAO()
    dao.upsert_many([_item(code="300390", url="u1"),
                     _item(code="000792", title="盐湖提价", url="u2")])
    assert len(dao.for_codes(["300390"])) == 1
    assert len(dao.for_codes(["300390", "000792"])) == 2
    assert dao.unsummarized_count() == 2
    dao.apply_sentiment([{"id": dao.for_codes(["300390"])[0]["id"],
                          "sentiment": "利好", "brief": "订单落地"}])
    assert dao.unsummarized_count() == 1


def test_digest_roundtrip(tmp_db):
    from src.models.news import NewsDAO
    dao = NewsDAO()
    dao.save_digest("2026-10-19", "# 舆情日报", overall="偏中性", model="m",
                    item_count=7, usage={"prompt_tokens": 10})
    row = dao.get_digest("2026-10-19")
    assert row["overall"] == "偏中性" and row["item_count"] == 7
    assert json.loads(row["usage_json"])["prompt_tokens"] == 10
    assert dao.latest_digest()["digest_date"] == "2026-10-19"
    dao.save_digest("2026-10-19", "# 覆盖")   # 同日覆盖
    assert dao.get_digest("2026-10-19")["content_md"] == "# 覆盖"


# ── 源 ───────────────────────────────────────────────────────

def test_normalize_time_variants():
    from src.collector.news_fetcher import _normalize_time
    assert _normalize_time("2026-10-19 14:30:00") == "2026-10-19 14:30"
    assert _normalize_time("20261019143000") == "2026-10-19 14:30"
    assert _normalize_time("20261019") == "2026-10-19"
    assert _normalize_time("") == ""
    assert _normalize_time(None) == ""


def test_source_registry_and_unknown():
    from src.collector.news_fetcher import get_source
    assert get_source("akshare_em") is not None
    assert get_source("no-such-source") is None


def test_fetch_news_swallows_source_error(monkeypatch):
    """源挂了要回空，不能拖垮调用方"""
    from src.collector import news_fetcher

    class Boom:
        name = "boom"

        def fetch(self, code, name="", limit=10):
            raise RuntimeError("down")

    news_fetcher._REGISTRY["boom"] = Boom
    out = news_fetcher.fetch_news([("300390", "x")], source="boom")
    assert out == []


def test_fetch_news_dedupes(monkeypatch):
    from src.collector import news_fetcher

    class Same:
        name = "same"

        def fetch(self, code, name="", limit=10):
            return [_item(url="u1", title="同一条")]

    news_fetcher._REGISTRY["same"] = Same
    out = news_fetcher.fetch_news([("300390", "x"), ("000792", "y")],
                                  source="same")
    assert len(out) == 1


def test_target_codes_dedupes(client):
    from src.models.database import WatchlistDAO
    from src.models.ai_watchlist import AiWatchlistDAO
    WatchlistDAO().add("300390", "天华新能")
    AiWatchlistDAO().add("300390", "天华新能")
    AiWatchlistDAO().add("000792", "盐湖股份")
    from src.collector.news_fetcher import target_codes
    codes = [c for c, _ in target_codes()]
    assert codes.count("300390") == 1
    assert "000792" in codes


# ── AI 摘要器 ────────────────────────────────────────────────

class _FakeAnalyzer:
    """按调用次序回放：第一次=判定 JSON，第二次=Markdown 正文。"""
    configured = True

    def __init__(self, *texts):
        self.texts = list(texts) if texts else ["{}"]
        self.prompts = []

    def ask_raw(self, prompt, system=None):
        self.prompts.append(prompt)
        t = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        return (t, "fake-model", {"prompt_tokens": 100, "completion_tokens": 50})


def _items():

    return [{"id": 1, "code": "300390", "name": "天华新能",
             "title": "公司获海外大单", "summary": "",
             "published_at": "2026-10-19 09:00"},
            {"id": 2, "code": "000792", "name": "盐湖股份",
             "title": "行业综述", "summary": "",
             "published_at": "2026-10-19 09:30"}]


def test_summarize_news_ok():
    from src.analyzer.news_summarizer import summarize_news
    payload = json.dumps({
        "judgments": [{"id": 1, "sentiment": "利好", "brief": "订单落地"},
                      {"id": 2, "sentiment": "中性", "brief": "综述"}],
        "overall": "整体偏正面",
    })
    body = "# 舆情日报 2026-10-19" + chr(10) + "## 逐股" + chr(10) + "天华新能获大单，短线偏正面。"
    az = _FakeAnalyzer(payload, body)
    res = summarize_news(az, _items(),
                         {"300390": "天华新能", "000792": "盐湖股份"},
                         "2026-10-19")
    assert res["overall"] == "整体偏正面"
    assert len(res["judgments"]) == 2
    assert res["model"] == "fake-model"
    assert res["content_md"] == body
    # 两次调用：第一次判定，第二次正文
    assert len(az.prompts) == 2
    assert "judgments" in az.prompts[0]
    assert "舆情日报" in az.prompts[1]


def test_summarize_rejects_unknown_ids_and_bad_sentiment():
    from src.analyzer.news_summarizer import summarize_news
    payload = json.dumps({
        "judgments": [{"id": 999, "sentiment": "利好", "brief": "越界"},
                      {"id": 1, "sentiment": "暴涨", "brief": "非法倾向"}],
    })
    res = summarize_news(_FakeAnalyzer(payload, "# 正文"), _items(), {},
                         "2026-10-19")
    assert [j["id"] for j in res["judgments"]] == [1]
    assert res["judgments"][0]["sentiment"] == "中性", "非法倾向一律归中性"


def test_summarize_handles_code_fence_and_garbage():
    from src.analyzer.news_summarizer import summarize_news
    fenced = "```json" + chr(10) + json.dumps({"overall": "中性"}) + chr(10) + "```"
    ok = summarize_news(_FakeAnalyzer(fenced, "# 正文"), _items(), {}, "2026-10-19")
    assert ok and ok["overall"] == "中性"
    # 判定调用坏掉但正文还在 → 仍要出日报（情绪退回按计数兜底）
    bad = summarize_news(_FakeAnalyzer("完全不是JSON", "# 正文"), _items(), {},
                         "2026-10-19")
    assert bad and bad["judgments"] == [] and bad["content_md"] == "# 正文"
    # 正文为空 → 视为失败
    assert summarize_news(_FakeAnalyzer("{}", ""), _items(), {}, "2026-10-19") is None


def test_summarize_requires_items_and_analyzer():
    from src.analyzer.news_summarizer import summarize_news
    assert summarize_news(_FakeAnalyzer("{}"), [], {}, "2026-10-19") is None
    assert summarize_news(None, _items(), {}, "2026-10-19") is None


def test_summarize_survives_llm_exception():
    from src.analyzer.news_summarizer import summarize_news

    class Boom:
        configured = True

        def ask_raw(self, prompt, system=None):
            raise RuntimeError("llm down")

    assert summarize_news(Boom(), _items(), {}, "2026-10-19") is None


# ── 路由 ──────────────────────────────────────────────────────

def test_api_news_latest_empty(client):
    r = client.get("/api/news/latest")
    assert r.status_code == 200 and r.json()["items"] == []


def test_api_news_ingest_and_list(client, monkeypatch):
    from src.models.news import NewsDAO
    from src.collector import news_fetcher

    monkeypatch.setattr(news_fetcher, "target_codes",
                        lambda **kw: [("300390", "天华新能")])
    monkeypatch.setattr(news_fetcher, "fetch_news",
                        lambda codes, source="", per_code=10: [_item()])
    # 用例保持 hermetic：只用个股源，不打真实市场源
    monkeypatch.setattr(news_fetcher, "sources_from_config",
                        lambda cfg: ["akshare_em"])
    r = client.post("/api/news/ingest", json={"per_code": 5})
    assert r.status_code == 200 and r.json()["added"] == 1
    got = client.get("/api/news/latest").json()
    assert got["count"] == 1 and got["items"][0]["title"]


def test_api_news_ingest_validates_per_code(client):
    assert client.post("/api/news/ingest",
                       json={"per_code": 0}).status_code == 400
    assert client.post("/api/news/ingest",
                       json={"per_code": 99}).status_code == 400


def test_api_news_summary_without_key(client, monkeypatch):
    from src.analyzer import ai_analyzer as mod

    monkeypatch.setattr(mod.AiAnalyzer, "configured", False, raising=False)
    r = client.post("/api/news/summary", json={})
    assert r.status_code == 400


def test_api_news_digest_none(client):
    r = client.get("/api/news/digest")
    assert r.status_code == 200 and r.json()["data"] is None


def test_ingest_respects_code_cooldown(client, monkeypatch):
    """冷却期内不重复打数据源（后台轮询的节流手段）。client 隔离到 tmp 库。"""
    from src.collector import news_fetcher

    monkeypatch.setattr(news_fetcher, "target_codes",
                        lambda **kw: [("300390", "天华新能"), ("000792", "盐湖股份")])
    calls = []

    def fake_fetch(codes, source="", per_code=10):
        calls.append([c for c, _ in codes])
        return [_item()] if codes else []

    monkeypatch.setattr(news_fetcher, "fetch_news", fake_fetch)
    r1 = news_fetcher.ingest(per_code=3, cooldown_min=60)
    assert r1["codes"] == 2 and r1["skipped"] == 0
    r2 = news_fetcher.ingest(per_code=3, cooldown_min=60)
    assert r2["skipped"] == 2 and r2["codes"] == 0, "冷却期内应全部跳过"
    assert len(calls) == 2, "仍会调用 fetch_news（对空列表不打网络）"
    r3 = news_fetcher.ingest(per_code=3, cooldown_min=0)
    assert r3["skipped"] == 0 and r3["codes"] == 2, "关掉冷却则全抓"


def test_recently_fetched_uses_db(client):
    from src.models.news import NewsDAO
    dao = NewsDAO()
    dao.upsert_many([_item(code="300390", url="u1")])
    dao.mark_fetched(["300390"])
    assert "300390" in dao.recently_fetched(["300390", "000792"], 60)
    assert dao.recently_fetched(["000792"], 60) == set(), "没抓过的票不算"
    assert dao.recently_fetched(["300390"], 0) == set(), "冷却为 0 则不过滤"



# ── 多源（个股层 + 市场层） ──────────────────────────────

def test_sources_from_config_prefers_list_and_keeps_legacy_str():
    from src.collector.news_fetcher import sources_from_config
    assert sources_from_config({"sources": ["akshare_em", "caixin_hot"]}) == [
        "akshare_em", "caixin_hot"]
    assert sources_from_config({"source": "akshare_em"}) == ["akshare_em"]
    assert sources_from_config({}) == ["akshare_em"], "缺配置时回退东财"
    assert sources_from_config({"sources": "caixin_hot"}) == ["caixin_hot"]


def test_caixin_is_market_level():
    from src.collector.news_fetcher import get_source
    src = get_source("caixin_hot")
    assert src is not None and src.market_level is True
    assert src.fetch("300390", "天华新能") == [], "市场级源不按股票抓"


def test_caixin_fetch_market_parses_headline_and_date(monkeypatch):
    import pandas as pd
    import akshare as ak
    from src.collector.news_fetcher import get_source

    df = pd.DataFrame([
        {"tag": "市场动态", "summary": "全球股票净卖出。后续正文。",
         "url": "https://database.caixin.com/2026-10-20/1024.html?cxapp=1"},
        {"tag": "商圈", "summary": "   ", "url": "https://x/2026-10-19/1.html"},
        {"tag": "数据通", "summary": "【本文系数据通用户提前专享】保险资金持续加仓。正文。",
         "url": "https://x/2026-10-18/2.html"},
    ])
    monkeypatch.setattr(ak, "stock_news_main_cx", lambda: df)
    items = get_source("caixin_hot").fetch_market(limit=10)
    assert len(items) == 2, "空正文应跳过"
    it = items[0]
    assert it["code"] == "" and it["name"] == "市场"
    assert it["title"] == "全球股票净卖出", "标题取正文首句"
    assert it["published_at"] == "2026-10-20", "时间从 URL 解析"
    assert it["url"] == "https://database.caixin.com/2026-10-20/1024.html", \
        "查询参数应去掉"
    assert items[1]["title"] == "保险资金持续加仓", "「【…】」前缀应剥掉"


def test_ingest_market_source_writes_market_rows(tmp_db, monkeypatch):
    from src.collector import news_fetcher
    from src.models.news import NewsDAO

    class FakeMarket:
        name = "fake_market"
        market_level = True

        def fetch(self, *a, **k):
            return []

        def fetch_market(self, limit=20):
            return [_item(code="", name="市场", title="财新要闻", url="m1")]

    monkeypatch.setitem(news_fetcher._REGISTRY, "fake_market", FakeMarket)
    r = news_fetcher.ingest(sources=["fake_market"], market_per_run=5,
                            cooldown_min=60)
    assert r["market_added"] == 1
    rows = NewsDAO().latest(only_market=True)
    assert len(rows) == 1 and rows[0]["code"] == ""
    assert rows[0]["name"] == "市场"

    r2 = news_fetcher.ingest(sources=["fake_market"], market_per_run=5,
                             cooldown_min=60)
    assert r2["market_skipped"] is True and r2["market_added"] == 0, \
        "市场源也有冷却"

    r3 = news_fetcher.ingest(sources=["fake_market"], market_per_run=5,
                             cooldown_min=60, market_cooldown_min=0)
    assert r3["market_skipped"] is False, "市场冷却可独立关掉"


def test_latest_can_filter_unsummarized_market(tmp_db):
    from src.models.news import NewsDAO
    dao = NewsDAO()
    dao.upsert_many([_item(code="", name="市场", title="市场一", url="m1")])
    assert len(dao.latest(only_market=True, unsummarized_only=True)) == 1
    row = dao.latest(only_market=True)[0]
    dao.apply_sentiment([{"id": row["id"], "sentiment": "中性", "brief": "x"}])
    assert dao.latest(only_market=True, unsummarized_only=True) == []
