"""腾讯批查单批重试（2026-11-12）。

产线实证：11-10 缺 80 只、11-12 缺 7 只，都是某一批 curl 返回空、
那批代码整天没进 daily_close 覆盖层。单批失败必须重试 + 如实告警。
"""

import logging

from src.collector import akshare_fetcher as ak


def _codes(n=5):
    return [f"60000{i}" for i in range(n)]


def test_single_batch_failure_is_retried(monkeypatch):
    """第一批第一次空 → 重试成功 → 结果不丢。"""
    calls = {"n": 0}

    def fake_curl(url, timeout=30):
        calls["n"] += 1
        return "" if calls["n"] == 1 else "OK"

    monkeypatch.setattr(ak, "curl_get", fake_curl)
    monkeypatch.setattr(ak, "split_tc_response", lambda raw: ["x"] if raw else [])
    monkeypatch.setattr(ak, "parse_tc_line", lambda val: {"code": "600000", "price": 1.0})
    out = ak.fetch_tencent_batch(_codes())
    assert calls["n"] == 2, "空响应必须重试一次"
    assert len(out) == 1, "重试成功后结果不得丢"


def test_all_retries_fail_warns_loudly(monkeypatch, caplog):
    """重试耗尽仍失败 → 如实告警"哪批缺多少只"，不静默少。"""
    monkeypatch.setattr(ak, "curl_get", lambda url, timeout=30: "")
    monkeypatch.setattr(ak, "split_tc_response", lambda raw: [])
    monkeypatch.setattr(ak, "parse_tc_line", lambda val: None)
    with caplog.at_level(logging.WARNING):
        out = ak.fetch_tencent_batch(_codes())
    assert out == []
    assert "缺失" in caplog.text and "5 只" in caplog.text


def test_second_batch_failure_does_not_lose_first(monkeypatch):
    """两批：第一批成功、第二批失败 → 第一批结果保留，第二批如实告警。"""
    batch_no = {"n": 0}

    def fake_curl(url, timeout=30):
        batch_no["n"] += 1
        return "OK" if batch_no["n"] == 1 else ""

    monkeypatch.setattr(ak, "curl_get", fake_curl)
    monkeypatch.setattr(ak, "split_tc_response", lambda raw: ["x"] if raw else [])
    monkeypatch.setattr(ak, "parse_tc_line", lambda val: {"code": "600000", "price": 1.0})
    # 85 只 = 2 批（80 + 5）
    out = ak.fetch_tencent_batch(_codes(85))
    assert len(out) == 1, "第一批成功就该保留"
    assert batch_no["n"] == 3, "第二批应重试一次（1 次失败 + 1 次重试）"
