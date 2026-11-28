"""新闻源扩充：时间归一化 + 财联社/东财快讯适配（假 DataFrame）。"""
from __future__ import annotations

import sys
from datetime import date, time
from types import SimpleNamespace

import pandas as pd

from src.collector import news_fetcher as nf


def test_normalize_time_accepts_objects():
    assert nf._normalize_time(date(2026, 11, 28)) == "2026-11-28"
    assert nf._normalize_time(time(9, 39, 31)) == ""
    assert nf._normalize_time("2026-11-28 12:31:21") == "2026-11-28 12:31"
    assert nf._normalize_time("2026-11-28") == "2026-11-28"
    assert nf._normalize_time(None) == ""


def test_cls_source_maps_date_and_time(monkeypatch):
    df = pd.DataFrame([{
        "标题": "证监会发布新规", "内容": "内容",
        "发布日期": date(2026, 11, 28), "发布时间": time(9, 39, 31)}])
    fake = SimpleNamespace(stock_info_global_cls=lambda: df)
    monkeypatch.setitem(sys.modules, "akshare", fake)
    items = nf.ClsTelegraphSource().fetch_market(limit=5)
    assert len(items) == 1
    assert items[0]["source"] == "cls_telegraph"
    assert items[0]["published_at"] == "2026-11-28 09:39"
    assert items[0]["url"] == ""


def test_em_global_maps_url_and_time(monkeypatch):
    df = pd.DataFrame([{
        "标题": "财经快讯", "摘要": "摘要", "发布时间": "2026-11-28 12:30:00",
        "链接": "https://finance.eastmoney.com/a/1.html?x=1"}])
    fake = SimpleNamespace(stock_info_global_em=lambda: df)
    monkeypatch.setitem(sys.modules, "akshare", fake)
    items = nf.EmGlobalSource().fetch_market(limit=5)
    assert items[0]["title"] == "财经快讯"
    assert items[0]["url"] == "https://finance.eastmoney.com/a/1.html"
    assert items[0]["published_at"] == "2026-11-28 12:30"


def test_sina_source_uses_headline(monkeypatch):
    df = pd.DataFrame([{
        "时间": "2026-11-28 12:31:21",
        "内容": "【全球股市情绪改善】有分析指出，金价反弹。"}])
    fake = SimpleNamespace(stock_info_global_sina=lambda: df)
    monkeypatch.setitem(sys.modules, "akshare", fake)
    items = nf.SinaGlobalSource().fetch_market(limit=5)
    assert items[0]["title"] == "全球股市情绪改善"
    assert items[0]["published_at"] == "2026-11-28 12:31"


def test_registry_knows_expanded_sources():
    names = {"caixin_hot", "cls_telegraph", "em_global", "sina_global",
             "ths_global", "futu_global"}
    assert names <= set(nf._REGISTRY)
    assert nf.sources_from_config(
        {"sources": ["cls_telegraph", "em_global"]}) == [
        "cls_telegraph", "em_global"]
